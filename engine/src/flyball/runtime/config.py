"""A rig as a file: links, devices and controllers, built in that order.

A tree of tagged configs. Links are declared once and named by the devices
that use them; a device entry is flyball's envelope around the driver's own
config, keyed by name; a controller is keyed by the address of
the signal it drives and names its source. Formats are
[flyball.foundation.files][]'s business; which driver and link kinds exist is
[flyball.model.catalog.Catalogs][]'s, read here via
[get_catalog][flyball.model.catalog.get_catalog] where a function has no way
to take one as a parameter (a pydantic classmethod, a validator), and as an
explicit `catalogs` argument (default: the same) where it does.

Every tag resolves to a real constructor, so the file validates against the
models the code is built from, including configs another package registered
through the `flyball.configs` entry point. The same file with `fake_text`
and `fake_registers` links runs without hardware.

A file may start from a **board**: a profile, an installed package's data or
kept outside any package, that declares the links a machine has and names
its pins. `board = "rpi5"` is looked up on the board path; the file's own
`links` are added to the profile's, and a device's `pin = "GPIO18"` becomes
the link and line the profile says.

The `readers`, `actuators` and `loops` sections of the legacy model no
longer parse; devices and controllers replace them (`book/src/7-reference/rig-file.md`).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator, model_validator
from pydantic.json_schema import GenerateJsonSchema

# Nothing built into flyball core registers a tag implicitly any more --
# `scpi`/`modbus` (extensions/visa, extensions/modbus), the Linux buses and
# chips, flyball-sim's sim_plant/sim_daq/sim_drive, and engine's own laws,
# feedforwards and generators (`control/configs.py`) all register through the
# `flyball.configs` entry point instead, read by `Catalogs.discover()`.
from flyball.control import (
    IMC,
    PI,
    PID,
    Affine,
    OnOff,
    OpenLoop,
    P,
    Scheduled,
    SlidingMode,
    SmithPredictor,
    Table,
)
from flyball.foundation.config import Config, discover_paths, discriminated_union
from flyball.foundation.device import RESERVED_NAMES, Device, DeviceEntry, DriverConfig, Signal
from flyball.foundation.errors import ConflictError, NotFoundError
from flyball.foundation.files import SUFFIXES, load_document
from flyball.foundation.time import Clock
from flyball.model.catalog import Catalogs, ensure_discovered, get_catalog
from flyball.model.feedforward import NoFeedforward, Setpoint
from flyball.rig import Rig

# `LawConfig`/`FeedforwardConfig` are static pydantic field types
# (`ControllerEntry` below), so they need every built-in law/feedforward at
# import time, not `get_catalog()` -- unlike `registered()`, which reads it
# lazily per call for devices/links (the extension point; a third-party
# driver may not be imported yet). Laws and feedforwards have no extension
# point today (nothing outside engine defines one, `control/configs.py`
# registers all of them), so importing the built-ins directly here is
# equivalent, and doesn't risk `Catalogs.discover()` re-entering this module
# through an extension's own import chain (`flyball_sim`, notably, imports
# `RigConfig` from here).
_LAWS = (OpenLoop, P, PI, PID, IMC, OnOff, SmithPredictor, Scheduled, SlidingMode)
_FEEDFORWARDS = (Setpoint, NoFeedforward, Affine, Table)
LawConfig = discriminated_union({law.tag: law for law in _LAWS}, "tag", lambda law: law.config)
FeedforwardConfig = discriminated_union(
    {ff.tag: ff for ff in _FEEDFORWARDS}, "tag", lambda ff: ff.config
)

Role = Literal["link", "driver"]

LEGACY_SECTIONS = ("readers", "actuators", "loops")
LEGACY_MESSAGE = (
    "readers/actuators/loops are no longer rig-file sections; devices and controllers"
    " replace them, see book/src/7-reference/rig-file.md"
)


# region Which tag plays which part


def role_of(config: type[Config[Any]]) -> Role:
    """A device driver or a link: a driver subclasses `DriverConfig`, anything else is a link."""
    return "driver" if issubclass(config, DriverConfig) else "link"


def registered(role: Role, catalogs: Catalogs | None = None) -> tuple[type[Config[Any]], ...]:
    """Every tagged config playing `role`, in tag order.

    Args:
        role: `"driver"` or `"link"`.
        catalogs: Default: [get_catalog][flyball.model.catalog.get_catalog].
    """
    catalogs = catalogs or get_catalog()
    catalog = catalogs.devices if role == "driver" else catalogs.links
    return tuple(catalog[tag] for tag in sorted(catalog.tags()))


# endregion
# region The models


class ControllerEntry(BaseModel):
    """A controller and how it regulates its target; keyed by the target's address in the file."""

    model_config = ConfigDict(extra="forbid")

    signal: str = Field(description="The source signal's address (a P signal).")
    law: LawConfig | None = None  # type: ignore[valid-type]
    feedforward: FeedforwardConfig | None = Field(  # type: ignore[valid-type]
        default=None,
        description="Maps the source's unit to the target's; the law adds to it."
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


_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ns|us|ms|s|m|h|d|w)?\s*$", re.IGNORECASE)
_DURATION_NS = {
    "ns": 1,
    "us": 1_000,
    "ms": 1_000_000,
    "s": 1_000_000_000,
    "m": 60_000_000_000,
    "h": 3_600_000_000_000,
    "d": 86_400_000_000_000,
    "w": 604_800_000_000_000,
}
_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(i?)b?\s*$", re.IGNORECASE)
_SIZE_EXPONENT = {"": 0, "k": 1, "m": 2, "g": 3, "t": 4}


def parse_duration_ns(text: str | int | float) -> int:
    """`1h`, `30m`, `90s`, `2d`, `500ms` -- or a bare number of seconds -- as nanoseconds.

    `0` (any spelling) is zero: the runner reads that as *off* or *forever*.
    Anything else raises `ValueError`.
    """
    if isinstance(text, (int, float)):
        return round(text * 1_000_000_000)
    match = _DURATION.match(text)
    if match is None:
        raise ValueError(f"{text!r} is not a duration: a number with ns, us, ms, s, m, h, d or w")
    number, unit = match.groups()
    return round(float(number) * _DURATION_NS[(unit or "s").lower()])


def parse_size_bytes(text: str | int | float) -> int:
    """`256MB`, `20GB`, `1.5GiB`, `4096` -- as bytes: decimal for `kB`..`TB`, binary with an `i`.

    `0` is zero, which the runner reads as *no cap*. Anything else raises `ValueError`.
    """
    if isinstance(text, (int, float)):
        return round(text)
    match = _SIZE.match(text)
    if match is None:
        raise ValueError(f"{text!r} is not a size: a number with kB, MB, GB, TB or KiB, MiB, ...")
    number, prefix, binary = match.groups()
    base = 1024 if binary else 1000
    return round(float(number) * base ** _SIZE_EXPONENT[prefix.lower()])


Anonymous = Literal["none", "read"]


class AuthConfig(BaseModel):
    """The `runner.auth` section: who may reach the runner, and for what.

    A *password* is for a person at the UI: the login page trades it for a
    session cookie, so the browser never keeps the secret. A *token* is for
    machines -- the CLI, `flyball-mcp`, a script -- sent as a bearer header.
    Either one turns the door on; with neither the runner is open. What a
    caller with neither may do is `anonymous`: nothing, or read. Levels are
    `none < read < operate`; a later scheme (several sign-ins, a part of the
    rig locked) changes who gets which level, not what a level admits.
    """

    model_config = ConfigDict(extra="forbid")

    password: str | None = Field(
        default=None,
        description="The password the login page takes: a `$scrypt$` line from `flyball password`,"
        " or the plain text.",
    )
    token: str | None = Field(
        default=None, description="Bearer token for the CLI, MCP clients and scripts."
    )
    anonymous: Anonymous = Field(
        default="none",
        description="What a caller with no session and no token may do: nothing, or read"
        " (every GET and every stream).",
    )
    session: str = Field(default="12h", description="How long a login lasts (`12h`, `30m`).")
    secret: str | None = Field(
        default=None,
        description="The key that signs sessions; default: a key file beside the store, else one"
        " made for the process (a restart then signs everyone out).",
    )

    @field_validator("session", mode="before")
    @classmethod
    def _duration(cls, value: Any) -> str:
        parse_duration_ns(value)
        return str(value)

    @property
    def enabled(self) -> bool:
        """Whether anyone is refused: a password or a token is set."""
        return bool(self.password or self.token)

    @property
    def session_s(self) -> float:
        return parse_duration_ns(self.session) / 1e9


class RunnerConfig(BaseModel):
    """The `runner:` section: how the process serves, not what the rig is.

    Everything here is fixed for the life of the process and says nothing
    about the equipment, so it may live in the rig file or in a file of its
    own that layers with it. A command-line flag overrides a value here.
    A path is relative to the first rig file's directory.
    """

    model_config = ConfigDict(extra="forbid")

    host: str = Field(default="127.0.0.1", description="Bind address; loopback unless reachable.")
    port: int = 8000
    log_level: str = "info"
    store: Path | None = Field(
        default=None, description="Sessions and versions; default <rig>.sqlite beside the file."
    )
    store_dir: Path | None = Field(
        default=None, description="Where stores live, one per rig by name: <dir>/<name>.sqlite."
    )
    programs: Path | None = Field(
        default=None, description="Program files to import; default programs/ beside the file."
    )
    tunings: Path | None = Field(
        default=None, description="Control-law configs; default tunings/ beside the file."
    )
    drivers: Path | None = Field(
        default=None, description="Driver .py files; default drivers/ beside the file."
    )
    auth: AuthConfig = Field(
        default_factory=AuthConfig,
        description="Who may reach the runner: password, token, anonymous.",
    )
    compose: bool = Field(default=False, description="Build up a hardware rig over the API.")
    mcp: bool = Field(default=True, description="Mount the MCP servers at /mcp.")
    root_path: str | None = Field(default=None, description="Serve under this path prefix.")
    allow_save: bool = Field(
        default=False, description="Let the API write rig files: a save to a path, a sim save."
    )
    allow_shutdown: bool = Field(
        default=False, description="Let the API stop or restart the runner."
    )
    keep: str = Field(
        default="1h",
        description="How much the scratch record holds while nothing is recorded, in the rig's"
        " clock (`1h`, `30m`); `0` keeps none.",
    )
    keep_size: str = Field(
        default="256MB",
        description="The most the scratch record may take on disk; the oldest goes first.",
    )
    retain: str = Field(
        default="0",
        description="Delete an unpinned session this long after it ended (`30d`); `0` keeps all.",
    )
    rotate: str = Field(
        default="0",
        description="Close a recording at this length and continue it in a new session (`24h`);"
        " `0` never rotates.",
    )
    max_store: str = Field(
        default="0",
        description="Keep the store under this size by deleting the oldest data, of any kind,"
        " never pinned (`20GB`); `0` sets no cap.",
    )
    run: dict[str, Any] = Field(
        default_factory=dict,
        description="Freeform defaults for `flyball run`'s own CLI flags (e.g. `serve_ui`,"
        ' `uv`) -- Go-CLI-only, never read or validated here; present only so `extra="forbid"`'
        " doesn't reject keys that belong to the Go binary, not the runner.",
    )

    @model_validator(mode="before")
    @classmethod
    def _token_alias(cls, data: Any) -> Any:
        # `runner.token` from before `auth:` existed, and `RunnerConfig(token=...)`: the same
        # thing as `auth.token`, so it moves there rather than failing `extra="forbid"`.
        if isinstance(data, dict) and "token" in data:
            data = dict(data)
            token = data.pop("token")
            auth = data.get("auth")
            auth = auth.model_dump() if isinstance(auth, BaseModel) else dict(auth or {})
            auth.setdefault("token", token)
            data["auth"] = auth
        return data

    @field_validator("keep", "retain", "rotate", mode="before")
    @classmethod
    def _duration(cls, value: Any) -> str:
        parse_duration_ns(value)  # a bad spelling fails here, not when the runner first sweeps
        return str(value)

    @field_validator("keep_size", "max_store", mode="before")
    @classmethod
    def _size(cls, value: Any) -> str:
        parse_size_bytes(value)
        return str(value)

    @property
    def keep_ns(self) -> int:
        return parse_duration_ns(self.keep)

    @property
    def keep_bytes(self) -> int:
        return parse_size_bytes(self.keep_size)

    @property
    def retain_ns(self) -> int:
        return parse_duration_ns(self.retain)

    @property
    def rotate_ns(self) -> int:
        return parse_duration_ns(self.rotate)

    @property
    def max_bytes(self) -> int:
        return parse_size_bytes(self.max_store)


def is_simulated(links: dict[str, Any]) -> bool:
    """Whether every link is a fake or a simulation, so time may be played with."""
    return all(
        (tag := getattr(link, "config_tag", None)) is not None
        and (tag.startswith("sim_") or tag.startswith("fake_"))
        for link in links.values()
    )


def resolve_live(path: str, root: Any) -> Any:
    """What a config field's `live` path points at in `root`, the object it is resolved against.

    The grammar: dot-separated keys walked from `root` (`output`,
    `stats.noise`, `readings.zone1.value`); a `*` segment fans out over
    every key at that level and yields a dict keyed by them
    (`outputs.*` -> `{"zone1": 603.7, ...}`, `readings.*.value`). None when
    a key is missing; a fan-out drops keys the rest of the path misses.

    Generic dict-path resolution, not simulation-specific itself; used by
    `flyball_sim.simulation.Simulation.live` to resolve a plant's `live`
    fields against its own description.
    """
    return _resolve_live(path.split(".") if path else [], root)


def _resolve_live(segments: list[str], node: Any) -> Any:
    if not segments:
        return node
    head, rest = segments[0], segments[1:]
    if not isinstance(node, dict):
        return None
    if head == "*":
        found = {key: _resolve_live(rest, value) for key, value in node.items()}
        return {key: value for key, value in found.items() if value is not None}
    return _resolve_live(rest, node.get(head))


class RigConfig(BaseModel):
    """The whole file.

    `model_validate` and `model_json_schema` on this class use
    [get_catalog][flyball.model.catalog.get_catalog] -- the tags registered
    there at the time of the call -- so a config registered after import is
    as valid in a file as a built-in one.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    board: str | None = Field(
        default=None,
        description="A board profile: a name on the board path, or a path to the file.",
    )
    recording: bool = Field(
        default=False, description="Open a recording session when the runner starts."
    )
    clock: ClockEntry | None = Field(
        default=None, description="Run the rig's time faster, or stepped; simulated rigs only."
    )
    links: dict[str, Any] = Field(default_factory=dict)
    devices: dict[str, DeviceEntry] = Field(default_factory=dict)
    controllers: dict[str, ControllerEntry] = Field(
        default_factory=dict, description="Keyed by the target signal's address."
    )
    runner: RunnerConfig | None = Field(default=None, exclude=True)
    """How the runner serves; not part of the rig, so not of its document or versions."""
    files: list[Path] = Field(default_factory=list, exclude=True)
    """The files this was loaded from, set by `load_rig_config`; not part of the document."""
    resumed: bool = Field(default=False, exclude=True)
    """Loaded from a stored rig version rather than the files (`flyball-runner --resume`)."""

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> RigConfig:  # type: ignore[override]
        if cls is RigConfig:
            return rig_model(get_catalog()).model_validate(obj, **kwargs)
        return super().model_validate(obj, **kwargs)

    @classmethod
    def model_json_schema(cls, **kwargs: Any) -> dict[str, Any]:  # type: ignore[override]
        if cls is RigConfig:
            catalogs = get_catalog()
            schema = rig_model(catalogs).model_json_schema(**kwargs)
            devices_schema, devices_defs = _devices_schema(catalogs)
            schema.setdefault("$defs", {}).update(devices_defs)
            schema["properties"]["devices"] = {
                **schema["properties"]["devices"],
                **devices_schema,
            }
            # The schema is for a file in an editor, and a file may be a layer: `extends`
            # is stripped before validation (`resolve_layers`), and a `null` entry deletes
            # what an earlier layer declared (`merge`) -- both are documented rig-file syntax.
            schema["properties"]["extends"] = {
                "type": "array",
                "items": {"type": "string"},
                "title": "Extends",
                "description": "This file's own bases, merged in order before its own keys.",
            }
            removed = {"type": "null", "title": "Removed by this layer"}
            for section in ("links", "devices", "controllers"):
                entry = schema["properties"][section].get("additionalProperties")
                if not isinstance(entry, dict):
                    continue
                # Appended to an existing `oneOf` rather than wrapping it: the dashboard reads
                # `devices…additionalProperties.oneOf` and `links…discriminator` as they are.
                if isinstance(entry.get("oneOf"), list):
                    entry["oneOf"] = [*entry["oneOf"], removed]
                else:
                    properties = schema["properties"][section]
                    properties["additionalProperties"] = {"oneOf": [entry, removed]}
            return schema
        return super().model_json_schema(**kwargs)

    @model_validator(mode="before")
    @classmethod
    def _no_legacy_sections(cls, data: Any) -> Any:
        """The legacy sections fail with one message that says where the new shape is."""
        if isinstance(data, dict) and any(section in data for section in LEGACY_SECTIONS):
            raise ValueError(LEGACY_MESSAGE)
        return data

    @model_validator(mode="after")
    def _consistent(self) -> RigConfig:
        """Everything named in the file is declared in it, once, in one namespace.

        Device names are the table's keys, so a duplicate is the loader's
        to refuse; what is checked here is a reserved name, an unknown
        driver, an undeclared link, and a controller address without a dot.
        Before anything is built, so the file fails with one clear message
        instead of a build-time error part-way through.
        """
        catalogs = get_catalog()
        for name, entry in self.devices.items():
            if name in RESERVED_NAMES:
                raise ConflictError(f"Name {name!r} is reserved as a route segment")
            driver = catalogs.devices.get(entry.driver)
            if driver is None:
                raise ValueError(f"device {name!r}: driver {entry.driver!r} is not registered")
            if not issubclass(driver, DriverConfig):
                raise ValueError(
                    f"device {name!r}: {entry.driver!r} is a {driver.__name__}, not a device driver"
                )
            link = entry.config.get("link")
            if isinstance(link, str) and link not in self.links:
                raise ValueError(f"link {link!r} is not declared; links are {sorted(self.links)}")
        for target, controller in self.controllers.items():
            if "." not in target:
                raise ValueError(f"controller {target!r} must be a 'node.signal' address")
            if "." not in controller.signal:
                raise ValueError(
                    f"controller {target!r}: signal {controller.signal!r}"
                    " must be a 'node.signal' address"
                )
        if sum(c.default for c in self.controllers.values()) > 1:
            raise ValueError("only one controller can be the default")
        if self.clock is not None and not is_simulated(self.links):
            raise ValueError("`clock` is only for a rig whose links are all sim_* or fake_*")
        return self

    @property
    def simulated(self) -> bool:
        return is_simulated(self.links)

    def build(
        self, clock: Clock | None = None, start: bool = True, catalogs: Catalogs | None = None
    ) -> Rig:
        """Links, then devices, then their bound inputs, then controllers.

        Args:
            clock: The rig's timebase. Default: what the file's `clock` says;
                a simulated rig with none gets a scaled clock at 1x, so its
                speed can be changed while it runs.
            start: Poll the devices on their periods. False adds them
                without polling, for a caller that will drive reads itself.
            catalogs: Where each device's driver is looked up. Default:
                [get_catalog][flyball.model.catalog.get_catalog].
        """
        catalogs = catalogs or get_catalog()
        if clock is None and self.simulated:
            from flyball_sim.clock import ScaledClock, SteppedClock

            entry = self.clock or ClockEntry()
            clock = SteppedClock() if entry.stepped else ScaledClock(entry.speed)

        rig = Rig(self.name)
        rig.link_entries = dict(self.links)
        rig.files = list(self.files)
        rig.header = {
            k: v
            for k, v in canonical(self).items()
            if k not in ("name", "links", "devices", "controllers")
        }
        if clock is not None:
            rig.clock = clock
        # Build everything before anything runs: a failure part-way leaves no
        # thread polling and no name claimed for a retry to trip on, and no
        # link (a serial port, a socket) held open behind it.
        built_devices: list[Device] = []
        built_links: dict[str, Any] = {}
        try:
            for name, config in self.links.items():
                built_links[name] = config.build()
            rig.links = built_links
            for name, entry in self.devices.items():
                device = entry.build(name, built_links, catalogs)
                rig.add_device(device)
                rig.entries[name] = entry
                built_devices.append(device)
            for name, entry in self.devices.items():
                if entry.bound:
                    rig.bind_inputs(rig.devices[name], entry.bound)
            for target_address, controller in self.controllers.items():
                target = rig.resolve(target_address)
                if not isinstance(target, Signal):
                    raise ValueError(f"controller {target_address!r} is not a signal")
                source_signal = rig.resolve(controller.signal)
                if not isinstance(source_signal, Signal):
                    raise ValueError(
                        f"controller {target_address!r}: signal {controller.signal!r}"
                        " is not a signal"
                    )
                rig.attach_controller(
                    target,
                    source_signal,
                    law=controller.law,
                    feedforward=controller.feedforward,
                    default=controller.default,
                    min_period_s=controller.min_period_s,
                )
        except Exception:
            for device in built_devices:
                rig.release(device.name)
            for link in built_links.values():
                close = getattr(link, "close", None)
                if close is not None:
                    with suppress(Exception):
                        close()
            raise
        if start:
            for device in built_devices:
                rig.start_polling(device)
        rig.loaded = rig.document()
        return rig


_models: dict[tuple[tuple[str, ...], tuple[str, ...]], type[RigConfig]] = {}


def rig_model(catalogs: Catalogs | None = None) -> type[RigConfig]:
    """[RigConfig][flyball.runtime.config.RigConfig] typed with every tag registered now.

    Built once per set of registered tags and cached, so validating many
    files costs one model.

    Args:
        catalogs: Default: [get_catalog][flyball.model.catalog.get_catalog].
    """
    catalogs = catalogs or get_catalog()
    key = (tuple(sorted(catalogs.devices.tags())), tuple(sorted(catalogs.links.tags())))
    if key not in _models:
        links = Config.union(*registered("link", catalogs))
        _models[key] = create_model(
            "RigConfig",
            __base__=RigConfig,
            links=(dict[str, links], Field(default_factory=dict)),  # type: ignore[valid-type]
        )
    return _models[key]


def _driver_configs(catalogs: Catalogs) -> tuple[type[DriverConfig[Any]], ...]:
    """Every registered device driver, in tag order."""
    return tuple(
        config for config in registered("driver", catalogs) if issubclass(config, DriverConfig)
    )


def _devices_schema(catalogs: Catalogs) -> tuple[dict[str, Any], dict[str, Any]]:
    """The `devices` property's schema, and the `$defs` it needs.

    Built by hand from the installed `Catalogs` rather than inferred: a
    device entry's driver settings sit flat beside the envelope or under
    `config` (`DeviceEntry`'s own before-validator normalises this, not a
    pydantic discriminated union), so pydantic alone cannot describe the two
    shapes as one type. `oneOf` per registered driver, each with a flat and a
    layered variant; before any driver registers, `devices` is
    just a plain `DeviceEntry` map.
    """
    base = DeviceEntry.model_json_schema(ref_template="#/$defs/{model}")
    defs: dict[str, Any] = dict(base.get("$defs", {}))
    envelope = {k: v for k, v in base["properties"].items() if k not in ("driver", "config")}
    drivers = _driver_configs(catalogs)
    if not drivers:
        defs["DeviceEntry"] = base
        return {"additionalProperties": {"$ref": "#/$defs/DeviceEntry"}}, defs
    variants = []
    for driver in drivers:
        driver_schema = driver.model_json_schema(ref_template="#/$defs/{model}")
        defs.update(driver_schema.pop("$defs", {}))
        driver_properties = driver_schema.get("properties", {})
        driver_envelope = {**envelope, "driver": {"const": driver.config_tag}}
        # Exactly one shape may match (`oneOf`): layered needs `config`, flat forbids it,
        # else a flat entry with no required driver fields satisfies both and an
        # editor reports "matches multiple schemas".
        layered = {
            "type": "object",
            "title": f"{driver.config_tag} (layered)",
            "properties": {**driver_envelope, "config": driver_schema},
            "required": ["driver", "config"],
        }
        flat = {
            "type": "object",
            "title": f"{driver.config_tag} (flat)",
            "properties": {**driver_envelope, **driver_properties},
            "required": ["driver", *driver_schema.get("required", [])],
            "not": {"required": ["config"]},
        }
        variants.append({"oneOf": [layered, flat]})
    # A layer may add to a device a base declared (`bound`, a label, one `config` key) without
    # repeating its driver: envelope keys only, `config` unconstrained, and no `driver`.
    overlay = {
        "type": "object",
        "title": "overlay of a device declared in a base",
        "properties": {**envelope, "config": {"type": "object"}},
        "not": {"required": ["driver"]},
    }
    return {"additionalProperties": {"oneOf": [*variants, overlay]}}, defs


def canonical(config: RigConfig) -> dict[str, Any]:
    """`config` as the canonical layered document -- what `rig check` prints.

    Every device entry already carries `config:` after `DeviceEntry`'s own
    flat-or-layered normalisation, in envelope-key order; dumping drops every
    `null`, since a format like TOML has no way to write one.
    """
    return config.model_dump(mode="json", exclude_none=True)


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

BOARD_DIRS_GROUP = "flyball.board_dirs"
"""Entry-point group an installed package registers its board profiles' directory under."""


def board_dirs(near: Path | None = None) -> Iterator[Path]:
    """Where a board name is looked up, in order.

    `$FLYBALL_BOARDS`; each installed package's own profiles (`extensions/linux`
    ships `rpi4`, `rpi5`, `beaglebone_black`, `generic`, `sim` this way); a
    `boards/` directory beside the rig file or in any directory above it, for
    profiles of your own; `~/.config/flyball/boards`; `/etc/flyball/boards`.
    """
    for entry in os.environ.get(BOARDS_ENV, "").split(os.pathsep):
        if entry:
            yield Path(entry).expanduser()
    yield from discover_paths(BOARD_DIRS_GROUP)
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

    A device entry with `pin = "LABEL"` -- flat beside the envelope, or
    under `config` -- gets the driver-config fields the board gives that
    label, where the entry keeps its driver config; fields the entry
    already has win. Unknown labels are an error.
    """
    out = dict(document)
    out["links"] = {**board.links, **document.get("links", {})}

    def fields_of(label: Any, where: str) -> dict[str, Any] | None:
        if not isinstance(label, str):
            return None
        try:
            return board.pins[label]
        except KeyError:
            raise NotFoundError(
                f"{where}: pin {label!r} is not on this board; it has {sorted(board.pins)}"
            ) from None

    def resolved(entry: dict[str, Any], where: str) -> dict[str, Any]:
        config = entry.get("config")
        if isinstance(config, dict):
            if (fields := fields_of(config.get("pin"), where)) is not None:
                config = {**fields, **{k: v for k, v in config.items() if k != "pin"}}
            if (fields := fields_of(entry.get("pin"), where)) is not None:
                config = {**fields, **config}
            return {**{k: v for k, v in entry.items() if k != "pin"}, "config": config}
        if (fields := fields_of(entry.get("pin"), where)) is not None:
            return {**fields, **{k: v for k, v in entry.items() if k != "pin"}}
        return entry

    devices = document.get("devices")
    if isinstance(devices, dict):
        out["devices"] = {
            name: resolved(entry, f"devices.{name}") if isinstance(entry, dict) else entry
            for name, entry in devices.items()
        }
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
    path_list.extend(saved_overlays(path_list[0]))
    document, files = resolve_layers(path_list, sets)
    board_name = document.get("board")
    if not isinstance(board_name, str):
        return document, files
    board_path = find_board(board_name, path_list[0].parent)
    return apply_board(document, load_board(board_path)), [*files, board_path]


def saved_overlays(first: Path) -> list[Path]:
    """The overlays the runner saved beside a rig's first file: `<file>.d/*.<suffix>`, sorted.

    What `POST /api/rig/save` writes by default; loaded after the files
    named on the command line, so a saved addition comes back next start.
    """
    directory = first.with_name(first.name + ".d")
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.suffix.lower() in SUFFIXES)


def load_rig_config(
    path_or_paths: str | Path | Sequence[str | Path], sets: Sequence[str] = ()
) -> RigConfig:
    """Read and validate a rig file, or a layered rig of several, `.toml`, `.yaml` or `.json`.

    Installed packages' configs are discovered first, so their tags are valid
    in the file; a `board` is applied before validation.
    """
    ensure_discovered()
    document, files = resolve_documents(path_or_paths, sets)
    config = RigConfig.model_validate(document)
    config.files = files
    return config


def load_rig(path_or_paths: str | Path | Sequence[str | Path], sets: Sequence[str] = ()) -> Rig:
    return load_rig_config(path_or_paths, sets).build()


def rig_schema() -> dict[str, Any]:
    """The rig file's JSON schema, for an editor, with every tag installed here."""
    ensure_discovered()
    schema = RigConfig.model_json_schema()
    schema["$schema"] = GenerateJsonSchema.schema_dialect
    return schema


# endregion

__all__ = [
    "BOARDS_ENV",
    "BOARD_DIRS_GROUP",
    "Board",
    "ClockEntry",
    "ControllerEntry",
    "RigConfig",
    "apply_board",
    "board_dirs",
    "canonical",
    "find_board",
    "is_simulated",
    "load_board",
    "load_rig",
    "load_rig_config",
    "registered",
    "resolve_document",
    "resolve_documents",
    "resolve_live",
    "rig_model",
    "rig_schema",
    "role_of",
]
