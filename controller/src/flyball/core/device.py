"""Devices: the things a rig is made of, and how they describe themselves.

A device has a tree of [signals][flyball.core.signal.Signal] (namespaces
group them), each readable, publishing or writable; a driver declares the
tree as `TREE` or binds one it computes, and implements `read` for the
publishing side and `commit` for the writable one. The rig file wraps every
device in the same [envelope][flyball.core.device.DeviceEntry] around the
driver's own [config][flyball.core.device.DriverConfig].

A device also has three tiers, told apart by who changes them:

- **config** -- what it was built from; changed only by rebuilding. A
  [Config][flyball.core.config.Config], so it also builds the device.
- **settings** -- what an operator or program re-sets while it runs, by a
  command.
- **state** -- what the device reports now, including any
  [Condition][flyball.core.device.Condition] currently true.

A **view** joins them at one instant for the wire, nested so a client can tell
what changes from what does not.

A device declares only its `config`, `settings` and `state` properties: their
return annotations are read on subclassing and checked. Methods marked
[command][flyball.core.device.command] are collected the same way.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, ClassVar, get_type_hints, overload

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator
from pydantic.errors import (
    PydanticInvalidForJsonSchema,
    PydanticSchemaGenerationError,
    PydanticUndefinedAnnotation,
)
from pydantic.json_schema import JsonSchemaMode

from .config import Config
from .errors import NotFoundError
from .signal import Access, Band, Node, NodeSpec, Reading, Sample, Signal, SignalSpec, WriteState


class Level(IntEnum):
    """How much a condition or an event matters. `logging`'s numbers, so they interleave."""

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40


@dataclass(frozen=True, slots=True)
class Condition:
    """Something true of a device now: offline, railed, overdriven, waiting.

    In the device's state while it holds; a late-joining client sees the
    present, not a log.
    """

    kind: str  # stable and machine-readable: "offline", "railed"
    level: Level
    message: str
    since_ns: int


@dataclass(frozen=True, slots=True)
class Event:
    """Something that happened, for a log: a step failed, a pump clamped a request, a reader died.

    A [Condition][flyball.core.device.Condition] is what is true now and lives
    in state; an event is a point in time and lives in a stream and the
    session store.
    """

    time_ns: int
    level: Level
    scope: str
    """Which part: `loop`, `actuator`, `reader`, `program`, `rig`."""
    subject: str
    """The loop, device or step it concerns."""
    kind: str
    """Stable and machine-readable: `step_failed`, `offline`, `clamped`."""
    message: str
    details: Any = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceState:
    """What a device reports now. Subclasses add their fields."""

    conditions: tuple[Condition, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceSettings:
    """What can be re-set while a device runs. A bare device has nothing."""


class DeviceConfig[D: "Device"](Config[D]):
    """What a device is built from, and what builds it.

    `label` is for people; `name` stays the identifier routes, programs and
    sessions use, so renaming what a heater is called on screen changes
    nothing that refers to it.
    """

    label: str | None = Field(
        default=None, description="A display name, e.g. 'Zone 1 heater'; `name` is the identifier."
    )

    def build(self) -> D:
        raise NotImplementedError(f"{type(self).__name__} cannot build a device")


@dataclass(frozen=True, slots=True)
class DeviceView[C: DeviceConfig[Any], T: DeviceSettings, S: DeviceState]:
    """A device at one instant: how it was built, how it is set, and what it reports."""

    config: C
    settings: T
    state: S


def _schemable(owner: type, attr: str, model: Any, mode: JsonSchemaMode) -> None:
    """Fail at class definition if pydantic cannot describe `model`."""
    try:
        TypeAdapter(model).json_schema(mode=mode)
    except PydanticUndefinedAnnotation:
        pass  # a forward reference; resolved on first real use
    except (PydanticSchemaGenerationError, PydanticInvalidForJsonSchema) as e:
        shown = getattr(model, "__name__", repr(model))
        raise TypeError(f"{owner.__name__}.{attr} ({shown}) has no JSON schema: {e}") from e


def _check_command_signature(owner: type, spec: CommandSpec) -> None:
    """Every argument and the return of a command must cross the wire.

    Checked at class definition, not on the first request.
    """
    try:
        hints = get_type_hints(spec.method)
    except NameError:
        return  # a forward reference; the server's model derives it later
    for name, annotation in hints.items():
        where = (
            f"{spec.method.__name__}() -> "
            if name == "return"
            else f"{spec.method.__name__}({name})"
        )
        if annotation is type(None):
            continue
        _schemable(owner, where, annotation, "serialization" if name == "return" else "validation")


def _declared_return(cls: type, prop: str) -> Any:
    """The return annotation of `cls`'s own `prop` property; None if not overridden here."""
    attr = cls.__dict__.get(prop)
    if not isinstance(attr, property) or attr.fget is None:
        return None
    try:
        return get_type_hints(attr.fget).get("return")
    except NameError:
        return None  # a forward reference; the inherited type stands


RESERVED_NAMES = frozenset({"schema"})
"""Path segments the server uses after an actuator's name; no command may take them."""


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """One method exposed as a command, marked by [command][flyball.core.device.command]."""

    tag: str
    method: Callable[..., Any]
    simulation: bool = False
    """Only meaningful on a simulated device -- a scripted fault, a disturbance. A UI keeps
    these on its simulation page, not beside the device's real commands."""

    @property
    def doc(self) -> str | None:
        return self.method.__doc__


@overload
def command[F: Callable[..., Any]](fn: F, /) -> F: ...
@overload
def command[F: Callable[..., Any]](
    *, tag: str | None = None, simulation: bool = False
) -> Callable[[F], F]: ...
def command(fn: Any = None, /, *, tag: str | None = None, simulation: bool = False) -> Any:
    """Mark an actuator method as a command, under its name or `tag`.

    `@command` or `@command(tag="stop")`. The method's signature is the
    command's. `simulation=True` marks one that only makes sense on a
    simulated device (a scripted fault, a disturbance): it is served like any
    other, but the schema says so, so a UI can keep it off the device's page.
    """

    def mark(f: Any) -> Any:
        f.__command__ = tag or f.__name__
        f.__simulation__ = simulation
        return f

    return mark(fn) if fn is not None else mark


class Device:
    """Something with a name, signals (each readable / publishing / writable), commands and state.

    Subclasses declare `TREE` (the driver's defaults, with each signal's
    access) and implement [read][flyball.core.device.Device.read] for R/P
    signals and/or [commit][flyball.core.device.Device.commit] for W ones. A
    bare sensor has only RP signals, a heater relay only W, a PSU has RPW
    ones. A driver whose tree depends on its config builds it in `__init__`
    and calls [bind][flyball.core.device.Device.bind] itself.

    `config_type`, `settings_type` and `state_type` are read off the property
    annotations on subclassing. `commands` collects every method marked
    [command][flyball.core.device.command], parents' included. A device
    declaring none still answers `view` with empty models.
    """

    TREE: ClassVar[tuple[NodeSpec | SignalSpec, ...]] = ()
    """The driver's declared tree; bound on construction when non-empty."""

    name: str
    label: str | None = None
    """A display name, from the rig file; None: show `name`."""
    poll_s: float | None = None
    """How often the runtime calls `read`, inherited down the tree; None: never polled."""
    root: Node
    """The device itself as a node; its address is the device name."""
    signals: dict[str, Signal]
    """Every leaf, by address relative to the device: `"dry.humidity"`."""
    nodes: dict[str, Node]
    """Every namespace, likewise: `"dry"`."""
    bound: dict[str, Signal | Node]
    """Inputs this device follows on other devices, by role (`"dry"`): a signal, or a whole
    namespace read as one message; the rig resolves them."""
    pending: dict[Signal, float]
    """What `apply` recorded since the last `commit`."""
    written: dict[Signal, WriteState]
    """The last state each W signal was committed to, for the wire."""
    config_type: ClassVar[type[DeviceConfig[Any]]] = DeviceConfig
    settings_type: ClassVar[type[DeviceSettings]] = DeviceSettings
    state_type: ClassVar[type[DeviceState]] = DeviceState
    commands: ClassVar[dict[str, CommandSpec]] = {}

    def __init__(self, name: str, label: str | None = None) -> None:
        self.name = name
        self.label = label
        self.bound = {}
        self.pending = {}
        self.written = {}
        if self.TREE:
            self.bind(self.TREE)

    # region Tree

    def bind(self, tree: Iterable[NodeSpec | SignalSpec]) -> None:
        """Make the bound tree from `tree`: `root`, `signals` and `nodes`.

        Once, at build: readings, samples and demands hold these objects by
        identity, and the rig file's overrides are applied onto them.
        """
        self.root = Node(spec=None, device=self, parent=None, address=self.name, path="")
        self._bind_under(self.root, tree)
        self.signals = {signal.path: signal for signal in self.root.walk()}
        self.nodes = {node.path: node for node in self.root.descendants()}

    def _bind_under(self, node: Node, specs: Iterable[NodeSpec | SignalSpec]) -> None:
        for spec in specs:
            address = f"{node.address}.{spec.name}"
            path = f"{node.path}.{spec.name}" if node.path else spec.name
            if spec.name in node.signals or spec.name in node.children:
                raise ValueError(f"'{address}' is declared twice")
            if isinstance(spec, SignalSpec):
                node.signals[spec.name] = Signal(
                    spec=spec, node=node, address=address, path=path, access=spec.access
                )
            else:
                child = Node(spec=spec, device=self, parent=node, address=address, path=path)
                node.children[spec.name] = child
                self._bind_under(child, spec.children)

    def _having(self, flag: Access) -> dict[str, Signal]:
        return {path: s for path, s in self.signals.items() if flag in s.access}

    @property
    def readable(self) -> dict[str, Signal]:
        return self._having(Access.R)

    @property
    def publishing(self) -> dict[str, Signal]:
        return self._having(Access.P)

    @property
    def writable(self) -> dict[str, Signal]:
        return self._having(Access.W)

    # endregion

    # region Read side

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """Poll the due signals under `node` (None: the whole device); one Sample per instant read.

        Strings never reach a driver: the rig resolves an address once and
        passes the bound object. Usually one Sample carrying every publishing
        signal, but a slow bus may yield them at different instants, a
        buffered instrument a backlog, and per-signal `poll_s` means only
        some are due at a given call. Yield nothing if none are. Raise
        HardwareError to go offline; the runtime records the condition and
        retries on the next poll.
        """
        raise NotImplementedError(f"{type(self).__name__} has nothing to read")

    # endregion

    # region Write side: two phases, as today's set_demand()/apply() split

    def apply(self, signal: Signal, time_ns: int, value: float) -> None:
        """Record one W value; no hardware I/O here.

        The mirror of `observe`: one signal, one instant, one value. The rig
        has already validated the whole demand this came from -- W signals
        under one node, in their own units, `together` groups complete,
        controller ownership, clamped to `limits` -- and fans it out one
        signal at a time, noting that this device was touched. The default
        stores into `pending`; most drivers need not override.
        """
        self.pending[signal] = value

    def observe(self, event: Reading | Sample) -> None:
        """A bound input changed: something on another device this one follows. Record it.

        A [Reading][flyball.core.signal.Reading] for a bound signal; for a
        bound node, the [Sample][flyball.core.signal.Sample] cut down to what
        lies under it, keyed relative to it, after the readings of that
        delivery. Only called on a device with something in `bound`; the
        default has none.
        """
        raise NotImplementedError(f"{type(self).__name__} follows no bound input")

    def commit(self, time_ns: int) -> Mapping[Signal, WriteState]:
        """Push everything recorded since the last commit to the hardware, once, and report it.

        The rig calls it once per delivery for every device it touched, and
        immediately after a manual demand. There is no dirty flag in the
        driver contract: the rig keeps the touched set. The default writes
        `pending` straight through [write_signal][flyball.core.device.Device.write_signal];
        a composite device (a blender) recomputes from all its inputs here,
        and may skip I/O when nothing changed value.
        """
        for signal, value in self.pending.items():
            self.write_signal(signal, value)
        return self.flush_pending()

    def write_signal(self, signal: Signal, value: float) -> None:
        """Put one committed value on the hardware. Default: nothing -- the device just holds it."""

    def flush_pending(self) -> dict[Signal, WriteState]:
        """A [WriteState][flyball.core.signal.WriteState] per pending signal; clears `pending`.

        `at_limit` when the value sits on a limit: the rig clamped it before
        `apply`. The states stay in `written` for the wire.
        """
        states = {signal: signal.write_state(value) for signal, value in self.pending.items()}
        self.written.update(states)
        self.pending.clear()
        return states

    # endregion

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        tiers: tuple[tuple[str, str, type, JsonSchemaMode], ...] = (
            ("config", "config_type", DeviceConfig, "validation"),  # arrives over the wire
            ("settings", "settings_type", DeviceSettings, "validation"),  # set by commands
            ("state", "state_type", DeviceState, "serialization"),  # only ever leaves
        )
        for prop, attr, base, mode in tiers:
            model = _declared_return(cls, prop)
            if model is None:
                continue
            if not (isinstance(model, type) and issubclass(model, base)):
                raise TypeError(
                    f"{cls.__name__}.{prop} must return a {base.__name__}, not {model!r}"
                )
            setattr(cls, attr, model)
            _schemable(cls, attr, model, mode)

        # Own dict, extended from the parent's: a subclass adds commands, and
        # marking one in a subclass must not leak into its siblings.
        cls.commands = dict(cls.commands)
        for attr_name, value in cls.__dict__.items():
            if (tag := getattr(value, "__command__", None)) is not None:
                if tag in RESERVED_NAMES:
                    raise ValueError(f"{cls.__name__}: {tag!r} is reserved as a route segment")
                if tag in cls.commands and cls.commands[tag].method.__name__ != attr_name:
                    raise ValueError(f"{cls.__name__}: command tag {tag!r} is already used")
                if not (value.__doc__ or "").strip():
                    # The CLI, the form and the schema all show it; without it
                    # they show a blank where the help should be.
                    raise TypeError(f"{cls.__name__}.{attr_name}: a command needs a docstring")
                spec = CommandSpec(tag, value, getattr(value, "__simulation__", False))
                _check_command_signature(cls, spec)
                cls.commands[tag] = spec

    # A subclass narrows these through its own return annotations -- that is
    # both what the checker sees and what `__init_subclass__` reads.
    @property
    def config(self) -> DeviceConfig[Any]:
        """Default: nothing to say. Override with the config the device was built from."""
        return DeviceConfig()

    @property
    def settings(self) -> DeviceSettings:
        """Default: nothing to set. Override alongside the commands that set things."""
        return DeviceSettings()

    @property
    def state(self) -> DeviceState:
        """Default: nothing to report. Override with what the device knows now."""
        return DeviceState()

    @property
    def view(self) -> DeviceView[Any, Any, Any]:
        return DeviceView(self.config, self.settings, self.state)


# region The rig-file envelope

ENVELOPE_KEYS = frozenset({"driver", "label", "poll_s", "signals", "bound", "config"})
"""The keys of a device entry that are flyball's, the same for every driver."""


class DriverConfig[D: Device](Config[D]):
    """A driver's own settings: what sits flat beside the envelope, or under `config`.

    The tagged model `driver:` selects (the tag is the driver name). It may
    not declare a field named like an envelope key, so flat and layered
    entries always mean the same thing; that is checked at import, like tag
    clashes. [DeviceConfig][flyball.core.device.DeviceConfig] is the legacy
    base and stays until the drivers move over.
    """

    link: str | None = Field(
        default=None, description="A transport (or a simulated plant), by name."
    )

    @classmethod
    def __pydantic_init_subclass__(cls, tag: str | None = None, **kwargs: Any) -> None:
        if clash := ENVELOPE_KEYS.intersection(cls.model_fields):
            raise TypeError(
                f"{cls.__name__}: {', '.join(sorted(clash))} is an envelope key,"
                " reserved for the rig file"
            )
        super().__pydantic_init_subclass__(tag=tag, **kwargs)

    def build(self, name: str, label: str | None = None) -> D:  # pyright: ignore[reportIncompatibleMethodOverride]  the envelope supplies the name
        raise NotImplementedError(f"{type(self).__name__} cannot build a device")


class SignalOverride(BaseModel):
    """The envelope's per-signal keys: metadata to override, access to remove.

    `access` names the set to keep (`"r"`); `readable`, `publishing` and
    `writable` drop one flag each and take only `false` -- the driver
    declares what it can honour, the file cannot add to it.
    """

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    range: Band | None = None
    precision: int | None = None
    warn: Band | None = None
    alarm: Band | None = None
    poll_s: float | None = None
    limits: Band | None = None
    together: frozenset[str] | None = None
    access: str | None = None
    readable: bool | None = None
    publishing: bool | None = None
    writable: bool | None = None

    @field_validator("access")
    @classmethod
    def _wire_form(cls, value: str | None) -> str | None:
        return None if value is None else str(Access.parse(value))

    @field_validator("readable", "publishing", "writable")
    @classmethod
    def _only_removes(cls, value: bool | None) -> bool | None:
        if value:
            raise ValueError(
                "only `false` is allowed: the driver declares the access it can honour"
            )
        return value


class NamespaceOverride(BaseModel):
    """The envelope of a namespace: the same keys again, and its own driver config."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    poll_s: float | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    """The namespace's own driver settings (an I²C address); the driver reads them at build."""
    signals: dict[str, SignalOverride | NamespaceOverride] = Field(default_factory=dict)


NamespaceOverride.model_rebuild()


class DeviceEntry(BaseModel):
    """The envelope of one device in the rig file: flyball's keys, the same for every driver.

    `driver:` picks the driver's config model by tag; the driver's own
    settings sit flat beside these keys or under `config`, and both parse to
    the same thing. If `config` is present it is the whole of the driver
    config and any other leftover key is an error.
    """

    model_config = ConfigDict(extra="forbid")

    driver: str
    label: str | None = None
    poll_s: float | None = None
    signals: dict[str, SignalOverride | NamespaceOverride] = Field(default_factory=dict)
    bound: dict[str, str] = Field(default_factory=dict)
    """Role -> address on another device; the rig resolves it."""
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _flat_or_layered(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        leftover = {key: value for key, value in data.items() if key not in ENVELOPE_KEYS}
        if not leftover:
            return data
        if "config" in data:
            raise ValueError(
                f"{', '.join(sorted(leftover))} beside `config`: the driver's settings go"
                " under `config` or flat beside the envelope, not both"
            )
        envelope = {key: value for key, value in data.items() if key in ENVELOPE_KEYS}
        return {**envelope, "config": leftover}

    def build(self, name: str, links: Mapping[str, Any] | None = None) -> Device:
        """Build the device `driver` describes and apply this envelope to it.

        The driver binds its tree; the overrides are then applied onto the
        bound objects in place, so nothing holds a stale reference. Unknown
        names and added access are errors that name the address. When the
        driver config's `link` names a key in `links`, it is substituted with
        the built object first -- exactly as `with_link` does for a legacy
        config; an undeclared name is a `NotFoundError` naming the device
        and the link.
        """
        driver = Config.registry.get(self.driver)
        if driver is None:
            raise ValueError(f"driver {self.driver!r} is not registered")
        if not issubclass(driver, DriverConfig):
            raise ValueError(f"driver {self.driver!r} is a {driver.__name__}, not a device driver")
        config = driver.model_validate(self.config)
        if isinstance(config.link, str):
            if links is None or config.link not in links:
                raise NotFoundError(f"device {name!r}: link {config.link!r} is not declared")
            config = config.model_copy(update={"link": links[config.link]})
        device = config.build(name, self.label)
        if self.label is not None:
            device.label = self.label
        if self.poll_s is not None:
            device.poll_s = self.poll_s
        _override_under(device.root, self.signals)
        return device


_SIGNAL_FIELDS = ("label", "range", "precision", "warn", "alarm", "poll_s", "limits", "together")
_NODE_FIELDS = ("label", "poll_s")


def _override_under(
    node: Node, overrides: Mapping[str, SignalOverride | NamespaceOverride]
) -> None:
    for name, override in overrides.items():
        address = f"{node.address}.{name}"
        if (signal := node.signals.get(name)) is not None:
            if isinstance(override, NamespaceOverride):
                raise ValueError(f"'{address}' is a signal, not a namespace")
            _override_signal(signal, override)
        elif (child := node.children.get(name)) is not None:
            if isinstance(override, SignalOverride):
                # `{poll_s: 5}` alone parses as a signal's override; on a
                # namespace it means the same thing.
                if extra := override.model_fields_set - set(_NODE_FIELDS):
                    raise ValueError(
                        f"'{address}' is a namespace: {', '.join(sorted(extra))} is a signal's"
                    )
                override = NamespaceOverride(label=override.label, poll_s=override.poll_s)
            changes = {f: v for f in _NODE_FIELDS if (v := getattr(override, f)) is not None}
            if changes:
                child.override(**changes)
            _override_under(child, override.signals)
        else:
            raise ValueError(f"'{address}' is not a signal or namespace of {node.device.name!r}")


def _override_signal(signal: Signal, override: SignalOverride) -> None:
    changes = {f: v for f in _SIGNAL_FIELDS if (v := getattr(override, f)) is not None}
    if changes:
        signal.override(**changes)
    value = signal.access.value if override.access is None else Access.parse(override.access).value
    for flag, keep in (
        (Access.R, override.readable),
        (Access.P, override.publishing),
        (Access.W, override.writable),
    ):
        if keep is False:
            value &= ~flag.value
    if value & Access.P.value and not value & Access.R.value:
        raise ValueError(f"Signal '{signal.address}': readable: false leaves it publishing")
    if value != signal.access.value:
        signal.restrict(Access(value))


# endregion
