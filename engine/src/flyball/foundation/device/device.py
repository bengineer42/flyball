"""Devices: the things a rig is made of, and how they describe themselves.

A device has a tree of [signals][flyball.foundation.device.signal.Signal] (namespaces
group them), each with a [Role][flyball.foundation.device.signal.Role]: a **demand** is
settable and has a current value, a **readout** is produced, a **setting** is
re-set by a command, a **config** is effective at build; an **input** is not
a signal of the device but another device's, which the rig binds to it.
Structure is declared once -- as descriptors in the class body
(`flows = Namespace(...)`, `dry_flow = flows.demand(...)`), or built from
config in `__init__` with the same factories and bound with
[bind][flyball.foundation.device.device.Device.bind]. On the class a descriptor is its
spec; on an instance it is the bound signal, whose `value` is read from the
[router][flyball.foundation.router.router.Router] and whose `push` puts one there.

A device that produces samples on a schedule is
[Readable][flyball.foundation.device.device.Readable] (`read`); one with demands is
[Committable][flyball.foundation.device.device.Committable] (`commit`: everything
recorded since the last commit goes to the hardware once). Methods marked
[command][flyball.foundation.device.commands.command] are what people and programs run;
the rig runs them, sets the device's `mode`, and records them.

Every device has `conditions`: an output the driver pushes what is true of
it now onto (railed, overdriven, waiting), beside what the runtime knows of
polling it. The rig file wraps every device in the same
[envelope][flyball.foundation.device.entry.DeviceEntry] around the driver's own
[config][flyball.foundation.device.device.DriverConfig].

Split across this package by concern: [state][flyball.foundation.device.state]
(`Condition`/`Event`), [commands][flyball.foundation.device.commands] (`@command`,
`CommandSpec`), [descriptors][flyball.foundation.device.descriptors] (`Namespace`,
`Demand`, `Readout`, ...), [building][flyball.foundation.device.building] (the
`__init_subclass__` helpers that turn descriptors into a tree and commands),
[entry][flyball.foundation.device.entry] (the rig file's envelope, `DeviceEntry`).
`DriverConfig` stays here rather than in `entry`: its generic bound
(`DriverConfig[D: Device]`) is evaluated at class-definition time, not lazily
like an annotation, so it needs `Device` as a real import -- keeping it here
instead of in `entry.py` (which needs `DriverConfig` too, for the `issubclass`
check in `DeviceEntry.build`) keeps the import direction one-way: `entry` ->
`device`, never the reverse.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any, ClassVar, get_type_hints

from pydantic import Field

from flyball.model.config import Config

from ..router.router import Router
from .building import _inputs, _last_of, _Leaf, _leaves, _link_params, _setter
from .commands import RESERVED_NAMES, CommandSpec, _check_command_signature, _schemable, command
from .descriptors import Descriptor, Input, Namespace, Readout, _descriptors
from .signal import (
    Access,
    Node,
    NodeSpec,
    Path,
    Role,
    Sample,
    Signal,
    SignalSpec,
    Value,
    WriteState,
)
from .state import Condition

__all__ = [
    "Committable",
    "Device",
    "DriverConfig",
    "Pending",
    "Readable",
    "command",
]


class Pending(dict[Signal, float]):
    """`Committable.pending`: what `apply` recorded, noting which demands the driver read.

    A plain dict to a driver. Looking a demand up (`[]`, `get`, `pop`, `in`,
    `signal.pending`) marks it read; walking the whole (`items`, `keys`,
    `values`, iterating, `copy`) marks all of them. After `commit` the rig
    asks which were never read -- a demand the driver did not look at, so
    nothing was set -- and `clear` forgets the marks with the demands.
    """

    __slots__ = ("_all", "_read")

    def __init__(self) -> None:
        super().__init__()
        self._read: set[Signal] = set()
        self._all = False

    def unread(self) -> list[Signal]:
        """The demands the driver has not looked at since the last `clear`, in apply order."""
        if self._all:
            return []
        return [signal for signal in dict.keys(self) if signal not in self._read]

    def _saw(self, key: object) -> None:
        if isinstance(key, Signal):
            self._read.add(key)

    def __getitem__(self, key: Signal) -> float:
        self._saw(key)
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        self._saw(key)
        return super().__contains__(key)

    def get(self, key: Signal, default: Any = None) -> Any:  # pyright: ignore[reportIncompatibleMethodOverride]
        self._saw(key)
        return super().get(key, default)

    def pop(self, key: Signal, *default: Any) -> Any:  # pyright: ignore[reportIncompatibleMethodOverride]
        self._saw(key)
        return super().pop(key, *default)

    def __iter__(self) -> Iterator[Signal]:
        self._all = True
        return super().__iter__()

    def keys(self) -> Any:
        self._all = True
        return super().keys()

    def values(self) -> Any:
        self._all = True
        return super().values()

    def items(self) -> Any:
        self._all = True
        return super().items()

    def copy(self) -> dict[Signal, float]:
        self._all = True
        return dict(super().items())

    def clear(self) -> None:
        super().clear()
        self._read.clear()
        self._all = False


def _declared_return(cls: type, prop: str) -> Any:
    """The return annotation of `cls`'s own `prop` property; None if not overridden here."""
    attr = cls.__dict__.get(prop)
    if not isinstance(attr, property) or attr.fget is None:
        return None
    try:
        return get_type_hints(attr.fget).get("return")
    except NameError:
        return None  # a forward reference; the inherited type stands


class Device:
    """Something with a name, a tree of signals (each with a role), commands, and conditions.

    Subclasses declare their structure as descriptors in the class body, or
    build it from config and call [bind][flyball.foundation.device.device.Device.bind].
    They implement [read][flyball.foundation.device.device.Readable.read] if they produce
    samples on a schedule and [commit][flyball.foundation.device.device.Committable.commit]
    if they have demands; `readable` and `writable` say which.

    `config_type` is read off the `config` property's annotation on
    subclassing. `commands` collects every method marked
    [command][flyball.foundation.device.commands.command], parents' included, plus a
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
    """Implements `read`: a [Readable][flyball.foundation.device.device.Readable]."""
    writable: ClassVar[bool] = False
    """Implements `commit`: a [Committable][flyball.foundation.device.device.Committable]."""

    conditions = Readout(
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
    pending: Pending
    """What `apply` recorded since the last `commit`; the rig clears it after each."""
    written: dict[Signal, WriteState]
    """The last state each W signal was committed to, for the wire."""
    router: Router
    """Where this device's values live: its own until a rig adds it, then the rig's."""
    config_type: ClassVar[type[DriverConfig[Any]]]
    commands: dict[str, CommandSpec] = {}  # ruff: ignore[mutable-class-default]  the class's; an instance copies and extends
    """Every command, by tag: the class's, plus a synthesised `set_<path>` for each demand of a
    tree computed at construction (a class's demands get theirs at definition)."""

    def __init__(self, name: str, label: str | None = None) -> None:
        self.name = name
        self.label = label
        self.bound = {}
        self.pending = Pending()
        self.written = {}
        self.router = Router()
        self._extended = False
        self._batch: dict[Signal, Value] | None = None
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
        specs = [spec for spec in specs if spec is not None]
        if not hasattr(self, "root"):
            self.root = Node(spec=None, device=self, parent=None, address=self.name, path=Path())
        elif self._extended:
            raise ValueError(f"{self.name!r} is already bound; a device's tree is static")
        else:
            self._extended = True
        self._bind_under(self.root, specs)
        self.signals = {str(signal.path): signal for signal in self.root.walk()}
        self.nodes = {str(node.path): node for node in self.root.descendants()}
        self.commands = dict(type(self).commands)
        # A demand a computed tree binds gets its setter here, as a class's do at definition.
        linked = {p.link for c in self.commands.values() for p in c.params.values() if p.link}
        setters = [
            _setter(type(self), _Leaf(path, signal.role, signal.spec))
            for path, signal in self.signals.items()
            if signal.role is Role.DEMAND and path not in linked
        ]
        new = [s for s in setters if s.tag not in self.commands]
        for setter in new:
            self.commands[setter.tag] = setter
        self.signals = {str(signal.path): signal for signal in self.root.walk()}
        self.nodes = {str(node.path): node for node in self.root.descendants()}
        for signal in self.root.walk():
            if signal.spec.initial is not None and signal.router.reading(signal) is None:
                signal.push(signal.spec.initial, 0)

    def _bind_under(
        self, node: Node, specs: Iterable[NodeSpec | SignalSpec], tags: dict[str, str] | None = None
    ) -> None:
        above = tags or {}
        for spec in specs:
            path = node.path / spec.name
            address = f"{self.name}.{path}"
            if spec.name in node.signals or spec.name in node.children:
                raise ValueError(f"'{address}' is declared twice")
            if isinstance(spec, SignalSpec):
                if above:  # a namespace's tags, under the signal's own
                    spec = replace(spec, tags={**above, **spec.tags})
                node.signals[spec.name] = Signal(
                    spec=spec, node=node, address=address, path=path, access=spec.access
                )
            else:
                child = Node(spec=spec, device=self, parent=node, address=address, path=path)
                node.children[spec.name] = child
                self._bind_under(child, spec.children, {**above, **spec.tags})

    def _having(self, flag: Access) -> dict[str, Signal]:
        return {path: s for path, s in self.signals.items() if flag in s.access}

    @property
    def readables(self) -> dict[str, Signal]:
        """Every signal with `R`, by path."""
        return self._having(Access.R)

    @property
    def publishing(self) -> dict[str, Signal]:
        """Every signal with `P`, by path: what polling samples and the recorder keeps."""
        return self._having(Access.P)

    @property
    def writables(self) -> dict[str, Signal]:
        """Every signal with `W`, by path."""
        return self._having(Access.W)

    @property
    def demands(self) -> dict[str, Signal]:
        """Every demand, by path."""
        return {path: s for path, s in self.signals.items() if s.role is Role.DEMAND}

    def sample(self, time_ns: int, **values: Value) -> Sample:
        """A sample on the root of the values given by descriptor attribute name."""
        return Sample(
            self.root,
            time_ns,
            {self.signals[self.DESCRIPTORS[attr].path]: value for attr, value in values.items()},
        )

    def push(self, time_ns: int | None = None, /, **values: Value) -> None:
        """Push several values at one instant by descriptor attribute: one sample, one delivery.

        `self.push(dry_flow=0.4, wet_flow=0.6)`; `time_ns` None is now. A
        value of None is nothing for that signal and is left out; a reading
        never carries None.
        """
        values = {attr: v for attr, v in values.items() if v is not None}
        if values:
            at = self.router.now_ns() if time_ns is None else time_ns
            self.router.push(self.sample(at, **values))

    @contextmanager
    def batch(self, time_ns: int | None = None) -> Iterator[None]:
        """Collect every `signal.push` made inside into one sample, delivered on exit.

        The other spelling of [push][flyball.foundation.device.device.Device.push], for
        when the values come from several places:

            with self.batch():
                self.dry_flow.push(flows.dry)
                self.mode.push(Mode.FLOWS)
        """
        if self._batch is not None:
            yield  # already inside one: it delivers
            return
        self._batch = {}
        try:
            yield
        finally:
            values, self._batch = self._batch, None
        if values:
            at = self.router.now_ns() if time_ns is None else time_ns
            self.router.push(Sample(self.root, at, values))

    def push_one(self, signal: Signal, value: Value, time_ns: int | None = None) -> None:
        """One value on one signal: into the open batch, else delivered now. `Signal.push`.

        None is nothing to push.
        """
        if value is None:
            return
        if self._batch is not None:
            self._batch[signal] = value
        else:
            self.router.push_reading(signal, value, time_ns)

    # endregion

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        model = _declared_return(cls, "config")
        if model is not None:
            if not (isinstance(model, type) and issubclass(model, DriverConfig)):
                raise TypeError(f"{cls.__name__}.config must return a DriverConfig, not {model!r}")
            cls.config_type = model
            _schemable(cls, "config_type", model, "validation")

        # The parents' tree, then a literal `TREE` of the class's own, then its
        # descriptors; `last` is rebuilt below. A literal `TREE` adds to the
        # base's `conditions`, it does not replace it.
        own = cls.__dict__.get("_declared", ())
        literal = cls.__dict__.get("TREE", ())
        parent = next((b.TREE for b in cls.__mro__[1:] if "TREE" in vars(b)), ())
        inherited = (spec for spec in parent if spec.name != "last")
        declared = (item.spec() for item in own if not isinstance(item, Input))
        cls.TREE = (*inherited, *literal, *(spec for spec in declared if spec is not None))
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
        [write_signal][flyball.foundation.device.device.Committable.write_signal]; a
        composite device (a blender) recomputes from all its inputs here,
        pushes its readbacks, and may skip I/O when nothing changed. The rig
        then reports each pending demand -- as the readback the driver
        pushed, or the committed value -- and clears `pending`.
        """
        for signal, value in self.pending.items():
            self.write_signal(signal, value)

    def write_signal(self, signal: Signal, value: float) -> None:
        """Put one committed value on the hardware. Default: nothing -- the device just holds it."""


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
