"""A rig as a file: links, readers, actuators and loops, built in that order.

A tree of tagged configs. Links are declared once and named by the devices
that use them; readers say how often to poll; loops name a channel as
`source.measurand`, an actuator by name, and a law by its config. Formats
are [flyball.core.files][]'s business; which device and link kinds exist is
the tag registry's.

Every tag resolves to a real constructor, so the file validates against the
models the code is built from, including configs another package registered
through the `flyball.configs` entry point. The same file with `fake_text`
and `fake_registers` links runs without hardware.

A file may start from a **board**: a profile, kept outside any package, that
declares the links a machine has and names its pins. `board = "rpi5"` is
looked up on the board path; the file's own `links` are added to the
profile's, and a device's `pin = "GPIO18"` becomes the link and line the
profile says.

"""

from __future__ import annotations

import os
import typing
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

# The built-in kinds register their tags when imported; a rig file can name
# them without the application importing anything.
import flyball.devices  # ruff: ignore[unused-import]
import flyball.hardware.links  # ruff: ignore[unused-import]
import flyball.integrations.pymeasure  # ruff: ignore[unused-import]
import flyball.integrations.qcodes  # ruff: ignore[unused-import]
import flyball.sim.devices  # ruff: ignore[unused-import]
from flyball.control import ControlLaws, Feedforwards
from flyball.core.clock import Clock
from flyball.core.config import Config, discover
from flyball.core.errors import ConflictError, NotFoundError
from flyball.core.files import SUFFIXES, load_document
from flyball.core.model import discriminated_union
from flyball.core.reading import Measurand, Reader, Source
from flyball.core.sink import RESERVED_NAMES, Actuator
from flyball.runtime.rig import Rig

LawConfig = discriminated_union(ControlLaws, "tag", lambda law: law.config)
FeedforwardConfig = discriminated_union(Feedforwards, "tag", lambda ff: ff.config)

Role = Literal["link", "reader", "actuator"]


# region Which tag plays which part


def _builds(config: type[Config[Any]]) -> Any:
    """What `config` builds: the `Config[T]` parameter if given, else `build`'s return type."""
    for base in config.__mro__:
        metadata = getattr(base, "__pydantic_generic_metadata__", None)
        if metadata and metadata.get("origin") is not None and metadata.get("args"):
            return metadata["args"][0]
    try:
        return typing.get_type_hints(config.build)["return"]
    except Exception:  # a forward reference that cannot be resolved: not a device
        return None


def role_of(config: type[Config[Any]]) -> Role:
    """Reader, actuator or link, from what the config builds."""
    built = _builds(config)
    if isinstance(built, type) and issubclass(built, Reader):
        return "reader"
    if isinstance(built, type) and issubclass(built, Actuator):
        return "actuator"
    return "link"


def registered(role: Role) -> tuple[type[Config[Any]], ...]:
    """Every tagged config playing `role`, in tag order."""
    return tuple(
        config for tag, config in sorted(Config.registry.items()) if role_of(config) == role
    )


# endregion
# region The models


class ReaderEntry(BaseModel):
    """A reader and how to run it."""

    model_config = ConfigDict(extra="forbid")

    device: Any
    period_s: float | None = Field(
        default=None, gt=0, description="Poll period; omit for a pushed reader."
    )


class LoopEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = Field(description="`source.measurand` of the controlled variable.")
    actuator: str = Field(description="The actuator by name.")
    law: LawConfig | None = None  # type: ignore[valid-type]
    feedforward: FeedforwardConfig | None = Field(  # type: ignore[valid-type]
        default=None,
        description="Maps the setpoint to a demand in the actuator's unit; the law adds to it."
        " Omit for the setpoint itself when the units agree, else none.",
    )
    default: bool = False
    min_period_s: float | None = Field(
        default=None,
        gt=0,
        description="Step the law at most this often; omit to step on every reading.",
    )


class ClockEntry(BaseModel):
    """How the rig's time runs. Only a rig with nothing real on it may run off wall time."""

    model_config = ConfigDict(extra="forbid")

    speed: float = Field(default=1.0, gt=0, description="Rig seconds per wall second.")
    stepped: bool = Field(
        default=False, description="Time moves only when stepped; for a batch run or a test."
    )


def is_simulated(links: dict[str, Any]) -> bool:
    """Whether every link is a fake or a simulation, so time may be played with."""
    return all(
        (tag := getattr(link, "config_tag", None)) is not None
        and (tag.startswith("sim_") or tag.startswith("fake_"))
        for link in links.values()
    )


class RigConfig(BaseModel):
    """The whole file.

    `model_validate` and `model_json_schema` on this class use the tags
    registered at the time of the call, so a config registered after import
    is as valid in a file as a built-in one.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    board: str | None = Field(
        default=None,
        description="A board profile: a name on the board path, or a path to the file.",
    )
    recording: bool = Field(
        default=False, description="Open a recording session when the daemon starts."
    )
    clock: ClockEntry | None = Field(
        default=None, description="Run the rig's time faster, or stepped; simulated rigs only."
    )
    links: dict[str, Any] = Field(default_factory=dict)
    readers: list[ReaderEntry] = Field(default_factory=list)
    actuators: list[Any] = Field(default_factory=list)
    loops: list[LoopEntry] = Field(default_factory=list)

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> RigConfig:  # type: ignore[override]
        if cls is RigConfig:
            return rig_model().model_validate(obj, **kwargs)
        return super().model_validate(obj, **kwargs)

    @classmethod
    def model_json_schema(cls, **kwargs: Any) -> dict[str, Any]:  # type: ignore[override]
        if cls is RigConfig:
            return rig_model().model_json_schema(**kwargs)
        return super().model_json_schema(**kwargs)

    @model_validator(mode="after")
    def _consistent(self) -> RigConfig:
        """Everything named in the file is declared in it, once, in one namespace.

        A reader, an actuator and a source (most readers' is just their own
        name under another hat, but `source` may say otherwise) all draw
        from the same names: no two may collide, rig-wide, and none may be a
        reserved route segment. Checked here, before anything is built, so
        the file fails with one clear message instead of a build-time error
        part-way through.
        """
        devices = [*(r.device for r in self.readers), *self.actuators]
        for link in (getattr(d, "link", None) for d in devices):
            if isinstance(link, str) and link not in self.links:
                raise ValueError(f"link {link!r} is not declared; links are {sorted(self.links)}")
        claimed: dict[str, str] = {}

        def claim(name: str, kind: str) -> None:
            if name in RESERVED_NAMES:
                raise ConflictError(f"Name {name!r} is reserved as a route segment")
            if (existing := claimed.get(name)) is not None:
                raise ConflictError(
                    f"Name {name!r} is already used by {existing} {name!r}"
                    f" (cannot also be {kind} {name!r})"
                )
            claimed[name] = kind

        for entry in self.readers:
            claim(entry.device.name, "reader")
            source = getattr(entry.device, "source", None)
            if source is not None and source != entry.device.name:
                claim(source, "source")
        for actuator in self.actuators:
            claim(actuator.name, "actuator")
        actuators = {a.name for a in self.actuators}
        driven: list[str] = []
        for loop in self.loops:
            if loop.actuator not in actuators:
                raise ValueError(
                    f"loop on {loop.channel!r} names actuator {loop.actuator!r}, not declared"
                )
            if loop.actuator in driven:
                raise ValueError(f"actuator {loop.actuator!r} is driven by two loops")
            driven.append(loop.actuator)
            if "." not in loop.channel:
                raise ValueError(f"loop channel {loop.channel!r} must be 'source.measurand'")
        if sum(loop.default for loop in self.loops) > 1:
            raise ValueError("only one loop can be the default")
        if self.clock is not None and not is_simulated(self.links):
            raise ValueError("`clock` is only for a rig whose links are all sim_* or fake_*")
        return self

    @property
    def simulated(self) -> bool:
        return is_simulated(self.links)

    def build(self, clock: Clock | None = None, start: bool = True) -> Rig:
        """Links, then readers, then actuators, then loops.

        Args:
            clock: The rig's timebase. Default: what the file's `clock` says;
                a simulated rig with none gets a scaled clock at 1x, so its
                speed can be changed while it runs.
            start: Poll the readers on their periods. False attaches them
                without polling, for a caller that will drive reads itself.
        """
        links = {name: config.build() for name, config in self.links.items()}
        if clock is None and self.simulated:
            from flyball.sim.clock import ScaledClock, SteppedClock

            entry = self.clock or ClockEntry()
            clock = SteppedClock() if entry.stepped else ScaledClock(entry.speed)

        def with_link(config: Any) -> Any:
            link = getattr(config, "link", None)
            return (
                config.model_copy(update={"link": links[link]}) if isinstance(link, str) else config
            )

        rig = Rig(self.name)
        rig.links = links
        if clock is not None:
            rig.clock = clock
        # Build everything before anything runs: a failure part-way leaves no
        # thread polling and no source name registered for a retry to trip on.
        readers: list[tuple[Reader, float | None]] = []
        try:
            for entry in self.readers:
                reader = with_link(entry.device).build()
                reader.label = entry.device.label
                sources = list(reader.sources)
                if entry.device.label and len(sources) == 1:
                    sources[0].label = entry.device.label  # one source: the device is it
                readers.append((reader, entry.period_s if start else None))
            for reader, _ in readers:
                rig.start_reader(reader)  # attached, not yet polled
            for config in self.actuators:
                actuator = with_link(config).build()
                actuator.label = config.label
                # A real actuator that cannot work out its own output_range (a sim one
                # does, from its limits) may state it in its config; generic by
                # attribute, since not every actuator config derives from ActuatorConfig.
                if (output_range := getattr(config, "output_range", None)) is not None:
                    actuator.output_range = output_range
                rig.add_actuator(actuator)
            for loop in self.loops:
                source_name, _, measurand_name = loop.channel.partition(".")
                try:
                    channel = Source.get(source_name)[Measurand.get(measurand_name)]
                    actuator = rig.actuators[loop.actuator]
                except (NotFoundError, KeyError) as e:
                    raise NotFoundError(
                        f"loop on {loop.channel!r} -> {loop.actuator!r}: {e}"
                    ) from e
                rig.attach_loop(
                    channel,
                    actuator,
                    law=loop.law,
                    default=loop.default,
                    min_period_s=loop.min_period_s,
                    feedforward=loop.feedforward,
                )
        except Exception:
            for reader, _ in readers:
                for source in reader.sources:
                    Source.forget(source.name)
            raise
        for reader, period in readers:
            if period is not None:
                rig.start_reader(reader, period)
        return rig


_models: dict[tuple[str, ...], type[RigConfig]] = {}


def rig_model() -> type[RigConfig]:
    """[RigConfig][flyball.runtime.config.RigConfig] typed with every tag registered now.

    Built once per set of registered tags and cached, so validating many
    files costs one model.
    """
    key = tuple(sorted(Config.registry))
    if key not in _models:
        links = Config.union(*registered("link"))
        readers = Config.union(*registered("reader"))
        actuators = Config.union(*registered("actuator"))
        entry = create_model("ReaderEntry", __base__=ReaderEntry, device=(readers, ...))
        _models[key] = create_model(
            "RigConfig",
            __base__=RigConfig,
            links=(dict[str, links], Field(default_factory=dict)),  # type: ignore[valid-type]
            readers=(list[entry], Field(default_factory=list)),  # type: ignore[valid-type]
            actuators=(list[actuators], Field(default_factory=list)),  # type: ignore[valid-type]
        )
    return _models[key]


# endregion
# region Boards


class Board(BaseModel):
    """A machine's I/O, as data: the links it has and what its pins are called.

    Lives in a file on the board path, not in a package: a new board is a
    file, and a wrong line number is an edit.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    links: dict[str, dict[str, Any]] = Field(default_factory=dict)
    pins: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Header label -> the device fields it stands for: `{link, line}`.",
    )


BOARDS_ENV = "FLYBALL_BOARDS"
"""Directories to look in for `<name>.toml`, colon-separated, before the defaults."""


def board_dirs(near: Path | None = None) -> Iterator[Path]:
    """Where a board name is looked up, in order.

    `$FLYBALL_BOARDS`; a `boards/` directory beside the rig file or in any
    directory above it; `~/.config/flyball/boards`; `/etc/flyball/boards`.
    """
    for entry in os.environ.get(BOARDS_ENV, "").split(os.pathsep):
        if entry:
            yield Path(entry).expanduser()
    if near is not None:
        for directory in (near.resolve(), *near.resolve().parents):
            yield directory / "boards"
    yield Path("~/.config/flyball/boards").expanduser()
    yield Path("/etc/flyball/boards")


def find_board(name: str, near: Path | None = None) -> Path:
    """The file for `name`: a path (relative to the rig file), or a name on the board path.

    Raises:
        NotFoundError: With every directory that was looked in.
    """
    base = near if near is not None else Path.cwd()
    if name.lower().endswith(SUFFIXES):
        path = base / name
        if path.is_file():
            return path
        raise NotFoundError(f"board file {path} does not exist")
    for directory in board_dirs(base):
        for suffix in SUFFIXES:
            if (candidate := directory / f"{name}{suffix}").is_file():
                return candidate
    looked = ", ".join(str(d) for d in board_dirs(base))
    raise NotFoundError(f"no board {name!r}; looked in {looked}")


def load_board(path: str | Path) -> Board:
    return Board.model_validate(load_document(path))


def apply_board(document: dict[str, Any], board: Board) -> dict[str, Any]:
    """The document with the board's links underneath its own and its pins resolved.

    A device entry with `pin = "LABEL"` gets the fields the board gives that
    label; fields the entry already has win. Unknown labels are an error.
    """
    out = dict(document)
    out["links"] = {**board.links, **document.get("links", {})}

    def resolved(entry: dict[str, Any], where: str) -> dict[str, Any]:
        label = entry.get("pin")
        if not isinstance(label, str):
            return entry
        try:
            fields = board.pins[label]
        except KeyError:
            raise NotFoundError(
                f"{where}: pin {label!r} is not on this board; it has {sorted(board.pins)}"
            ) from None
        return {**fields, **{k: v for k, v in entry.items() if k != "pin"}}

    out["readers"] = [
        {**r, "device": resolved(r["device"], f"readers[{i}]")}
        if isinstance(r, dict) and isinstance(r.get("device"), dict)
        else r
        for i, r in enumerate(document.get("readers", []))
    ]
    out["actuators"] = [
        resolved(a, f"actuators[{i}]") if isinstance(a, dict) else a
        for i, a in enumerate(document.get("actuators", []))
    ]
    return out


# endregion
# region Loading


def resolve_document(path: str | Path) -> tuple[dict[str, Any], Path | None]:
    """The plain document with its board applied, and the board file used, if any."""
    path = Path(path)
    document = load_document(path)
    if not isinstance(document, dict):
        raise ValueError(f"{path}: a rig file is a mapping")
    board_name = document.get("board")
    if not isinstance(board_name, str):
        return document, None
    board_path = find_board(board_name, path.parent)
    return apply_board(document, load_board(board_path)), board_path


def resolve_documents(
    paths: str | Path | Sequence[str | Path], sets: Sequence[str] = ()
) -> tuple[dict[str, Any], list[Path]]:
    """`resolve_document`, generalised to a rig laid out as several files.

    `paths` is one file or several, later overlaying earlier (see
    [resolve_layers][flyball.runtime.overlay.resolve_layers]); each file's
    own `extends` is resolved first. A `board` is looked up relative to the
    *first* file, exactly as `resolve_document` looks one up relative to its
    one file, and applied to the merged document.

    Returns:
        The merged, board-applied document, and every file that
        contributed: the layers, in the order first read, then the board
        file, if one was used.
    """
    from flyball.runtime.overlay import resolve_layers

    path_list = [Path(paths)] if isinstance(paths, (str, Path)) else [Path(p) for p in paths]
    document, files = resolve_layers(path_list, sets)
    board_name = document.get("board")
    if not isinstance(board_name, str):
        return document, files
    board_path = find_board(board_name, path_list[0].parent)
    return apply_board(document, load_board(board_path)), [*files, board_path]


def load_rig_config(
    path_or_paths: str | Path | Sequence[str | Path], sets: Sequence[str] = ()
) -> RigConfig:
    """Read and validate a rig file, or a layered rig of several, `.toml`, `.yaml` or `.json`.

    Installed packages' configs are discovered first, so their tags are valid
    in the file; a `board` is applied before validation.
    """
    discover()
    document, _ = resolve_documents(path_or_paths, sets)
    return RigConfig.model_validate(document)


def load_rig(path_or_paths: str | Path | Sequence[str | Path], sets: Sequence[str] = ()) -> Rig:
    return load_rig_config(path_or_paths, sets).build()


def rig_schema() -> dict[str, Any]:
    """The rig file's JSON schema, for an editor, with every tag installed here."""
    discover()
    return RigConfig.model_json_schema()


# endregion

__all__ = [
    "BOARDS_ENV",
    "Board",
    "ClockEntry",
    "LoopEntry",
    "ReaderEntry",
    "RigConfig",
    "apply_board",
    "board_dirs",
    "find_board",
    "is_simulated",
    "load_board",
    "load_rig",
    "load_rig_config",
    "registered",
    "resolve_document",
    "resolve_documents",
    "rig_model",
    "rig_schema",
    "role_of",
]
