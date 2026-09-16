"""Devices: the things a rig is made of, and how they describe themselves.

A device has a tree of [signals][flyball.core.signal.Signal] (namespaces
group them), each with a [Role][flyball.core.signal.Role]: a **demand** is
settable and has a current value, an **output** is produced, a **config** is
effective at build, an **input** is another device's signal the rig binds
to a role. Structure is declared once -- as descriptors in the class body
(`flows = Namespace(...)`, `dry_flow = flows.demand(...)`), or built from
config in `__init__` with the same factories and bound with
[bind][flyball.core.device.Device.bind]. On the class a descriptor is its
spec; on an instance it is the bound signal, whose `value` is read from the
[router][flyball.core.router.Router] and whose `push` puts one there.

A device that produces samples on a schedule is
[Readable][flyball.core.device.Readable] (`read`); one with demands is
[Committable][flyball.core.device.Committable] (`commit`: everything
recorded since the last commit goes to the hardware once). Methods marked
[command][flyball.core.device.command] are what people and programs run;
the rig runs them, sets the device's `mode`, and records them.

Every device has `conditions`: an output the driver pushes what is true of
it now onto (railed, overdriven, waiting), beside what the runtime knows of
polling it. The rig file wraps every device in the same
[envelope][flyball.core.device.DeviceEntry] around the driver's own
[config][flyball.core.device.DriverConfig].
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Annotated, Any, ClassVar, Self, get_args, get_origin, get_type_hints, overload

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator
from pydantic.errors import (
    PydanticInvalidForJsonSchema,
    PydanticSchemaGenerationError,
    PydanticUndefinedAnnotation,
)
from pydantic.json_schema import JsonSchemaMode

from .config import Config
from .errors import NotFoundError, NotReadyError
from .quantity import Quantity
from .router import Router
from .signal import (
    Access,
    Band,
    Bound,
    Node,
    NodeSpec,
    Path,
    Role,
    Sample,
    Section,
    Signal,
    SignalRef,
    SignalSpec,
    Value,
    WriteState,
)
from .units.si import Unitless


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
"""Route segments the server uses after a device's name; no device or command may take them."""


@dataclass(frozen=True, slots=True)
class Param:
    """One argument of a command: its type, and the demand it is a value for, if any."""

    name: str
    annotation: Any
    link: str | None = None
    """The path of the demand this argument sets: from a descriptor in an `Annotated[...]`
    annotation, or a parameter named like a descriptor of the class. The rig fills a missing
    argument from its current value and clamps it to the signal's limits; the schema shows its
    unit, limits and address."""
    default: Any = inspect.Parameter.empty

    @property
    def required(self) -> bool:
        return self.default is inspect.Parameter.empty and self.link is None


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """One method exposed as a command, marked by [command][flyball.core.device.command]."""

    tag: str
    method: Callable[..., Any]
    params: dict[str, Param] = field(default_factory=dict)
    simulation: bool = False
    """Only meaningful on a simulated device -- a scripted fault, a disturbance. A UI keeps
    these on its simulation page, not beside the device's real commands."""
    commit: bool = False
    """The method only records; the rig commits the device afterwards. Most commands do their
    own I/O and need none."""
    mode: Any = None
    """What the device's `mode` output becomes when this runs, if it has one."""
    interrupts: bool = False
    """Puts a controller driving one of the device's demands into manual and runs (`stop`,
    a manual flow); without it, such a command is refused while the controller is active."""
    demand_of: str | None = None
    """For a synthesised `set_<name>`: the path of the demand it sets; the rig routes it through
    its demand path."""

    @property
    def doc(self) -> str | None:
        return self.method.__doc__


@overload
def command[F: Callable[..., Any]](fn: F, /) -> F: ...
@overload
def command[F: Callable[..., Any]](
    *,
    tag: str | None = None,
    simulation: bool = False,
    commit: bool = False,
    mode: Any = None,
    interrupts: bool = False,
) -> Callable[[F], F]: ...
def command(
    fn: Any = None,
    /,
    *,
    tag: str | None = None,
    simulation: bool = False,
    commit: bool = False,
    mode: Any = None,
    interrupts: bool = False,
) -> Any:
    """Mark a device method as a command, under its name or `tag`.

    `@command` or `@command(tag="stop")`. The method's signature is the
    command's; an argument annotated `Annotated[<type>, <descriptor>]` (or
    named like a descriptor) is a value for that demand -- legal in the
    class body, since the descriptor's name is already bound there. `mode`
    is what the device's `mode` output becomes when it runs. `commit=True`
    for a method that only records and needs the device committed after.
    `interrupts=True` puts a controller
    driving the device into manual and runs; without it the command is
    refused while one is active. `simulation=True` marks one that only
    makes sense on a simulated device (a scripted fault, a disturbance): it
    is served like any other, but the schema says so, so a UI can keep it
    off the device's page.
    """

    def mark(f: Any) -> Any:
        f.__command__ = tag or f.__name__
        f.__command_options__ = {
            "simulation": simulation,
            "commit": commit,
            "mode": mode,
            "interrupts": interrupts,
        }
        return f

    return mark(fn) if fn is not None else mark


# region Descriptors


class Namespace:
    """A namespace declared in a class body, or built from config: a builder for a NodeSpec.

    `flows = Namespace("flows", "Flows")` on the class; `self.flows` is the
    bound [Node][flyball.core.signal.Node]. Its `demand`, `output`, `config`
    and `input` make the signals under it.
    """

    def __init__(
        self,
        name: str,
        label: str = "",
        *,
        atomic: bool = False,
        poll_s: float | None = None,
        parent: Namespace | None = None,
    ) -> None:
        # Instance attributes only: a class-level annotation of a type that
        # has `__get__` reads as a descriptor to a checker.
        self.name = name
        self.label = label
        self.atomic = atomic
        self.poll_s = poll_s
        self.parent = parent
        self.children: list[Namespace | Descriptor[Any]] = []
        self.attr: str | None = None
        if parent is not None:
            parent.children.append(self)

    @property
    def path(self) -> str:
        return self.name if self.parent is None else f"{self.parent.path}.{self.name}"

    def namespace(self, name: str, label: str = "", **options: Any) -> Namespace:
        return Namespace(name, label, parent=self, **options)

    def demand(self, name: str | Section, label: str = "", *args: Any, **meta: Any) -> Demand:
        return Demand(name, label, *args, parent=self, **meta)

    def output(self, name: str | Section, label: str = "", *args: Any, **meta: Any) -> Output:
        return Output(name, label, *args, parent=self, **meta)

    def setting(self, name: str | Section, label: str = "", *args: Any, **meta: Any) -> Setting:
        return Setting(name, label, *args, parent=self, **meta)

    def config(self, name: str | Section, label: str = "", *args: Any, **meta: Any) -> ConfigSignal:
        return ConfigSignal(name, label, *args, parent=self, **meta)

    def input(self, name: str | Section, label: str = "", *args: Any, **meta: Any) -> Input:
        return Input(name, label, *args, parent=self, **meta)

    def spec(self) -> NodeSpec:
        return NodeSpec(
            name=self.name,
            label=self.label,
            atomic=self.atomic,
            poll_s=self.poll_s,
            children=tuple(c.spec() for c in self.children if not isinstance(c, Input)),
        )

    def __set_name__(self, owner: type, attr: str) -> None:
        self.attr = attr
        if self.parent is None:
            _declare(owner, self)

    @overload
    def __get__(self, instance: None, owner: type) -> Namespace: ...
    @overload
    def __get__(self, instance: Device, owner: type) -> Node: ...
    def __get__(self, instance: Device | None, owner: type) -> Namespace | Node:
        if instance is None:
            return self
        return instance.nodes[self.path]

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.path!r})"


class Descriptor[B]:
    """A signal declared in a class body, or built from config: a builder for a SignalSpec.

    `dry_flow = flows.demand("dry", "Dry pump flow", FLOW, limits=(0.0, dry_max_flow))`
    on the class; `self.dry_flow` is the bound [Signal][flyball.core.signal.Signal].
    A [Section][flyball.core.signal.Section] in place of the name gives the
    segment and tags the signal. `quantity` None: the name, unitless (a mode, a
    count). A limit may be another descriptor of the same device: its
    current value bounds this one.
    """

    role: ClassVar[Role]

    def __init__(
        self,
        name: str | Section,
        label: str = "",
        quantity: Quantity | None = None,
        vtype: Any = float,
        *,
        access: Access | None = None,
        parent: Namespace | None = None,
        **meta: Any,
    ) -> None:
        self.section: Section | None
        if isinstance(name, Section):
            self.section, self.name = name, name.name
            if not label:
                label = name.label
        else:
            self.section, self.name = meta.pop("section", None), name
        self.label = label
        self.quantity = Quantity(self.name, Unitless) if quantity is None else quantity
        self.vtype = vtype
        self.access = self.role.access if access is None else Access.check(access)
        self.meta = meta
        self.parent = parent
        self.attr: str | None = None
        if parent is not None:
            parent.children.append(self)

    @property
    def path(self) -> str:
        return self.name if self.parent is None else f"{self.parent.path}.{self.name}"

    def spec(self) -> SignalSpec:
        meta = dict(self.meta)
        if (limits := meta.get("limits")) is not None:
            meta["limits"] = tuple(_bound(b) for b in limits)
        return SignalSpec(
            name=self.name,
            quantity=self.quantity,
            access=self.access,
            role=self.role,
            section=self.section,
            vtype=self.vtype,
            label=self.label,
            **meta,
        )

    def __set_name__(self, owner: type, attr: str) -> None:
        self.attr = attr
        if self.parent is None:
            _declare(owner, self)

    @overload
    def __get__(self, instance: None, owner: type) -> Self: ...
    @overload
    def __get__(self, instance: Device, owner: type) -> B: ...
    def __get__(self, instance: Device | None, owner: type) -> Self | B:
        if instance is None:
            return self
        return self.on(instance)

    def on(self, device: Device) -> B:
        """What this descriptor is on an instance: the bound signal."""
        return device.signals[self.path]  # type: ignore[return-value]

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.path!r})"


def _bound(bound: Any) -> Bound:
    """A limit as the spec carries it: a number, or a reference to a descriptor's signal."""
    if isinstance(bound, Descriptor):
        return SignalRef(bound.path)
    if isinstance(bound, SignalRef):
        return bound
    return float(bound)


class Demand(Descriptor[Signal]):
    """Settable, with a current value that updates; what a controller drives. `RPW`."""

    role = Role.DEMAND


class Output(Descriptor[Signal]):
    """Produced, never set: a measurement, a derived value, a mode. `RP`."""

    role = Role.OUTPUT


class Setting(Descriptor[Signal]):
    """Re-set by a command while the device runs; shown, not driven. `RP`."""

    role = Role.SETTING


class ConfigSignal(Descriptor[Signal]):
    """Effective at build, shown, never set at run time: the driver pushes it once. `R`."""

    role = Role.CONFIG


class BoundInput:
    """An input on an instance: the source signal the rig bound, and its current value."""

    __slots__ = ("device", "input")

    def __init__(self, device: Device, input: Input) -> None:
        self.device = device
        self.input: Any = input  # not a class-level Descriptor annotation: see Namespace

    @property
    def signal(self) -> Signal | None:
        """The bound source, or None until the rig binds one."""
        bound = self.device.bound.get(self.input.name)
        return bound if isinstance(bound, Signal) else None

    @property
    def value(self) -> Value:
        """The source's newest value; the input's default before one arrives.

        Raises:
            NotReadyError: Nothing bound or read, and no default.
        """
        if (signal := self.signal) is not None and (reading := signal.reading) is not None:
            return reading.value
        default = self.input.default
        if isinstance(default, Descriptor):
            return self.device.signals[default.path].value
        if default is None:
            raise NotReadyError(f"{self.device.name}.{self.input.name}: nothing has been read")
        return default


class Input(Descriptor[BoundInput]):
    """Another device's signal, bound by the rig to this role (`bound: {dry: ...}`).

    Not in the device's tree: `self.dry_supply` is the source signal once
    bound, and reads `default` (a number, or a config descriptor) before
    that or when nothing has been read on it yet.
    """

    role = Role.INPUT

    def __init__(
        self,
        name: str | Section,
        label: str = "",
        quantity: Quantity | None = None,
        vtype: Any = float,
        *,
        default: Any = None,
        parent: Namespace | None = None,
        **meta: Any,
    ) -> None:
        super().__init__(name, label, quantity, vtype, parent=parent, **meta)
        self.default = default

    def on(self, device: Device) -> BoundInput:
        return BoundInput(device, self)


def _declare(owner: type, item: Namespace | Descriptor[Any]) -> None:
    """Note a top-level descriptor on its class, in declaration order, for `__init_subclass__`."""
    declared = owner.__dict__.get("_declared")
    if declared is None:
        declared = []
        owner._declared = declared  # type: ignore[attr-defined]
    declared.append(item)


def _descriptors(cls: type) -> dict[str, Descriptor[Any]]:
    """Every signal descriptor reachable from `cls` and its bases, by attribute name."""
    found: dict[str, Descriptor[Any]] = {}
    for base in reversed(cls.__mro__):
        for item in base.__dict__.get("_declared", ()):
            _collect(item, found)
    return found


def _collect(item: Namespace | Descriptor[Any], found: dict[str, Descriptor[Any]]) -> None:
    if isinstance(item, Descriptor):
        if item.attr is not None:
            found[item.attr] = item
    else:
        for child in item.children:
            _collect(child, found)


# endregion


def _inputs(items: Iterable[Namespace | Descriptor[Any]]) -> Iterator[Input]:
    for item in items:
        if isinstance(item, Input):
            yield item
        elif isinstance(item, Namespace):
            yield from _inputs(item.children)


@dataclass(frozen=True, slots=True)
class _Leaf:
    path: str
    role: Role
    spec: SignalSpec


def _leaves(tree: Iterable[NodeSpec | SignalSpec], above: str = "") -> Iterator[_Leaf]:
    for spec in tree:
        path = f"{above}.{spec.name}" if above else spec.name
        if isinstance(spec, SignalSpec):
            yield _Leaf(path, spec.role, spec)
        else:
            yield from _leaves(spec.children, path)


def _link_params(cls: type[Device], fn: Callable[..., Any]) -> dict[str, Param]:
    """Each argument of `fn` with the demand it is for: `Annotated[...]` first, then by name.

    Resolves the annotations against the class body, so `Annotated[Flow,
    dry_flow]` finds the descriptor, and writes the resolved ones back onto the
    function: whatever builds a request model from it later needs no
    class namespace.
    """
    try:
        hints = get_type_hints(fn, include_extras=True, localns=dict(vars(cls)))
    except NameError:
        return {}  # a forward reference; the server's model derives it later
    fn.__annotations__ = hints
    descriptors = cls.DESCRIPTORS
    params: dict[str, Param] = {}
    for name, parameter in list(inspect.signature(fn).parameters.items())[1:]:
        annotation = hints.get(name, Any)
        link: Descriptor[Any] | None = None
        if get_origin(annotation) is Annotated:
            link = next((m for m in get_args(annotation)[1:] if isinstance(m, Descriptor)), None)
        if link is None and (d := descriptors.get(name)) is not None and d.role is Role.DEMAND:
            link = d
        params[name] = Param(
            name, annotation, None if link is None else link.path, parameter.default
        )
    return params


def _setter(cls: type[Device], leaf: _Leaf) -> CommandSpec:
    """`set_<path>(value)` for a demand no command sets; the rig routes it to its demand path."""
    tag = "set_" + leaf.path.replace(".", "_")
    label = leaf.spec.label or leaf.spec.name.replace("_", " ")

    def setter(self: Device, value: float) -> None:
        raise NotImplementedError("a synthesised setter runs through the rig's demand path")

    setter.__name__ = tag
    setter.__qualname__ = f"{cls.__qualname__}.{tag}"
    setter.__doc__ = f"Set {label}."
    setter.__annotations__ = {"value": leaf.spec.vtype, "return": None}
    param = Param("value", leaf.spec.vtype, None, inspect.Parameter.empty)
    return CommandSpec(tag, setter, {"value": param}, demand_of=leaf.path)


def _last_of(cls: type[Device]) -> NodeSpec:
    """`last.<tag>`: when each command last ran and with what, for the wire and the record."""
    return NodeSpec(
        name="last",
        label="Last run",
        children=tuple(
            SignalSpec(
                name=tag,
                quantity=Quantity(tag, Unitless),
                access=Access.RP,
                vtype=dict[str, Any],
                label=tag.replace("_", " "),
            )
            for tag, spec in cls.commands.items()
            if not spec.simulation and spec.demand_of is None
        ),
    )


class Device:
    """Something with a name, a tree of signals (each with a role), commands, and conditions.

    Subclasses declare their structure as descriptors in the class body, or
    build it from config and call [bind][flyball.core.device.Device.bind].
    They implement [read][flyball.core.device.Readable.read] if they produce
    samples on a schedule and [commit][flyball.core.device.Committable.commit]
    if they have demands; `readable` and `writable` say which.

    `config_type` is read off the `config` property's annotation on
    subclassing. `commands` collects every method marked
    [command][flyball.core.device.command], parents' included, plus a
    synthesised `set_<name>` for every demand no command sets.
    """

    TREE: ClassVar[tuple[NodeSpec | SignalSpec, ...]] = ()
    """The declared tree, built from the class's descriptors (parents' first); bound on
    construction. A driver whose tree depends on its config binds more in `__init__`."""
    INPUTS: ClassVar[dict[str, Input]] = {}
    """The declared inputs by role, for the rig to bind and the schema to show."""
    DESCRIPTORS: ClassVar[dict[str, Descriptor[Any]]] = {}
    """Every signal descriptor by attribute name, for linking command arguments."""
    blocking: ClassVar[bool] = False
    """Whether `commit` may wait on a bus. The rig then runs it on a thread of its own, so a
    delivery never waits: the write states arrive when the write completes."""
    atomic: ClassVar[bool] = False
    """The root is read (and written) as one sample: a single-transaction device, an SHT4x."""
    readable: ClassVar[bool] = False
    """Implements `read`: a [Readable][flyball.core.device.Readable]."""
    writable: ClassVar[bool] = False
    """Implements `commit`: a [Committable][flyball.core.device.Committable]."""

    conditions = Output(
        "conditions", "Conditions", vtype=tuple[Condition, ...], access=Access.RP, initial=()
    )
    """What the driver says is true of the device now; the runtime's `offline` / `slow` are
    kept beside it, not on it."""

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
    router: Router
    """Where this device's values live: its own until a rig adds it, then the rig's."""
    config_type: ClassVar[type[DriverConfig[Any]]]
    commands: ClassVar[dict[str, CommandSpec]] = {}

    def __init__(self, name: str, label: str | None = None) -> None:
        self.name = name
        self.label = label
        self.bound = {}
        self.pending = {}
        self.written = {}
        self.router = Router()
        self._extended = False
        self.bind(self.TREE)

    # region Tree

    def bind(self, tree: Iterable[NodeSpec | SignalSpec | Namespace | Descriptor[Any]]) -> None:
        """Make the bound tree from `tree`: `root`, `signals` and `nodes`.

        Once, at build: readings, samples and demands hold these objects by
        identity, and the rig file's overrides are applied onto them. The
        tree is static for the life of the device; a device whose signals
        change is replaced, not rebound. A driver that computes its tree
        from config binds it once, on top of the class's; `Namespace` and
        descriptor builders are accepted beside specs. Signals with an
        `initial` value get it pushed at once.

        Raises:
            ValueError: The device already has a computed tree bound.
        """
        specs = [
            item.spec() if isinstance(item, (Namespace, Descriptor)) else item for item in tree
        ]
        if not hasattr(self, "root"):
            self.root = Node(spec=None, device=self, parent=None, address=self.name, path=Path())
        elif self._extended:
            raise ValueError(f"{self.name!r} is already bound; a device's tree is static")
        else:
            self._extended = True
        self._bind_under(self.root, specs)
        self.signals = {str(signal.path): signal for signal in self.root.walk()}
        self.nodes = {str(node.path): node for node in self.root.descendants()}
        for signal in self.root.walk():
            if signal.spec.initial is not None and signal.router.reading(signal) is None:
                signal.push(signal.spec.initial, 0)

    def _bind_under(self, node: Node, specs: Iterable[NodeSpec | SignalSpec]) -> None:
        for spec in specs:
            path = node.path / spec.name
            address = f"{self.name}.{path}"
            if spec.name in node.signals or spec.name in node.children:
                raise ValueError(f"'{address}' is declared twice")
            if isinstance(spec, SignalSpec):
                if spec.role is Role.INPUT:
                    raise ValueError(f"'{address}' is an input: bound by the rig, not in the tree")
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
    def readables(self) -> dict[str, Signal]:
        return self._having(Access.R)

    @property
    def publishing(self) -> dict[str, Signal]:
        return self._having(Access.P)

    @property
    def writables(self) -> dict[str, Signal]:
        return self._having(Access.W)

    @property
    def demands(self) -> dict[str, Signal]:
        return {path: s for path, s in self.signals.items() if s.role is Role.DEMAND}

    # endregion

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        model = _declared_return(cls, "config")
        if model is not None:
            if not (isinstance(model, type) and issubclass(model, DriverConfig)):
                raise TypeError(f"{cls.__name__}.config must return a DriverConfig, not {model!r}")
            cls.config_type = model
            _schemable(cls, "config_type", model, "validation")

        # The class's own descriptors, after its parents'; `last` is rebuilt below.
        own = cls.__dict__.get("_declared", ())
        inherited = (spec for spec in cls.TREE if spec.name != "last")
        cls.TREE = (*inherited, *(item.spec() for item in own if not isinstance(item, Input)))
        cls.INPUTS = {**cls.INPUTS, **{i.name: i for i in _inputs(own)}}
        cls.DESCRIPTORS = _descriptors(cls)
        cls.readable = callable(getattr(cls, "read", None))
        cls.writable = callable(getattr(cls, "commit", None))

        # Own dict, extended from the parent's: a subclass adds commands, and
        # marking one in a subclass must not leak into its siblings.
        cls.commands = {t: c for t, c in cls.commands.items() if c.demand_of is None}
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
                params = _link_params(cls, value)
                spec = CommandSpec(tag, value, params, **value.__command_options__)
                _check_command_signature(cls, spec)
                cls.commands[tag] = spec
        linked = {p.link for c in cls.commands.values() for p in c.params.values() if p.link}
        for leaf in _leaves(cls.TREE):
            if leaf.role is Role.DEMAND and leaf.path not in linked:
                setter = _setter(cls, leaf)
                cls.commands[setter.tag] = setter
        if any(not c.simulation and c.demand_of is None for c in cls.commands.values()):
            cls.TREE = (*cls.TREE, _last_of(cls))

    # A subclass narrows this through its own return annotation -- that is
    # both what the checker sees and what `__init_subclass__` reads.
    @property
    def config(self) -> DriverConfig[Any]:
        """Default: nothing to say. Override with the config the device was built from."""
        return DriverConfig()


Device.TREE = tuple(item.spec() for item in Device.__dict__["_declared"])  # `conditions`
Device.DESCRIPTORS = _descriptors(Device)


class Readable(Device):
    """A device that produces samples on a schedule: implements `read`."""

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """Poll the due signals under `node` (None: the whole device); one Sample per instant read.

        Strings never reach a driver, nor leave one: the rig resolves an
        address once and passes the bound object, and a Sample's keys are
        the bound signals (`self.signals["dry.humidity"]`, or the
        descriptor `self.humidity`). Usually one Sample carrying every
        publishing signal, but a slow bus may yield them at different
        instants, a buffered instrument a backlog, and per-signal `poll_s`
        means only some are due at a given call. Yield nothing if none are.
        Raise HardwareError to go offline; the runtime records the condition
        and retries on the next poll.
        """
        raise NotImplementedError(f"{type(self).__name__} has nothing to read")

    def sample(self, time_ns: int, **values: Value) -> Sample:
        """A sample on the root of the values given by descriptor attribute name."""
        return Sample(
            self.root,
            time_ns,
            {self.signals[self.DESCRIPTORS[attr].path]: value for attr, value in values.items()},
        )


class Committable(Device):
    """A device with demands: `apply` records one, `commit` puts everything recorded on the bus.

    Two phases, so however many of a controller's output, a manual demand
    and a changed input arrive in one delivery cost one hardware write.
    """

    def apply(self, signal: Signal, time_ns: int, value: float) -> None:
        """Record one demand; no hardware I/O here.

        The rig has already validated the whole demand this came from --
        demands under one node, in their own units, controller ownership,
        clamped to `limits` -- and fans it out one signal at a time, noting
        that this device was touched. The default stores into `pending`;
        most drivers need not override.
        """
        self.pending[signal] = value

    def commit(self, time_ns: int) -> None:
        """Push everything recorded since the last commit to the hardware, once.

        The rig calls it once per delivery for every device it touched --
        a demand applied, an input landed -- and immediately after a manual
        demand. There is no dirty flag in the driver contract: the rig keeps
        the touched set. The default writes `pending` straight through
        [write_signal][flyball.core.device.Committable.write_signal]; a
        composite device (a blender) recomputes from all its inputs here,
        pushes its readbacks, and may skip I/O when nothing changed. The rig
        then reports each pending demand -- as the readback the driver
        pushed, or the committed value -- and clears `pending`.
        """
        for signal, value in self.pending.items():
            self.write_signal(signal, value)

    def write_signal(self, signal: Signal, value: float) -> None:
        """Put one committed value on the hardware. Default: nothing -- the device just holds it."""


# region The rig-file envelope

ENVELOPE_KEYS = frozenset({"driver", "label", "poll_s", "signals", "bound", "config"})
"""The keys of a device entry that are flyball's, the same for every driver."""


class DriverConfig[D: Device](Config[D]):
    """A driver's own settings: what sits flat beside the envelope, or under `config`.

    The tagged model `driver:` selects (the tag is the driver name). It may
    not declare a field named like an envelope key, so flat and layered
    entries always mean the same thing; that is checked at import, like tag
    clashes.
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


Device.config_type = DriverConfig  # declared above it; the bare device's tier


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
    """The envelope of a namespace: label, period and the overrides of what is under it.

    A namespace's own driver settings (an I²C address) are not here: the
    driver declares its namespaces in its own config, typed, and the
    envelope only overrides what the driver declared.
    """

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    poll_s: float | None = None
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
        the built object first; an undeclared name is a `NotFoundError`
        naming the device and the link.
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


_SIGNAL_FIELDS = ("label", "range", "precision", "warn", "alarm", "poll_s", "limits")
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
