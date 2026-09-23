"""Signals: what a device declares, what it is bound to, and what moves on them.

A driver declares its tree once, as [SignalSpec][flyball.foundation.device.signal.SignalSpec]
leaves grouped by [NodeSpec][flyball.foundation.device.signal.NodeSpec] namespaces, each
leaf with an [Access][flyball.foundation.device.signal.Access] set. Binding the tree to a
device (`Device.bind`) makes one [Node][flyball.foundation.device.signal.Node] or
[Signal][flyball.foundation.device.signal.Signal] per spec, each carrying its address; they
are identity-hashed and created once, so a
[Reading][flyball.foundation.device.signal.Reading], a
[Sample][flyball.foundation.device.signal.Sample]
or a [Demand][flyball.foundation.device.signal.Demand] holds a reference and nothing on the
hot path looks a name up. Addresses are parsed once, at the boundary.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum, Flag, StrEnum, auto
from typing import TYPE_CHECKING, Any

from ..errors import NotFoundError
from ..quantities import Unit
from ..quantities.quantity import Quantity
from ..time.clock import Rate

if TYPE_CHECKING:
    from ..router.router import Router
    from .device import Device


type Value = Any
"""What a reading carries: a float for a measurement, an enum member for a mode, a list or a
dataclass for a structure. The signal's `vtype` says which; the wire's `dtype` names it."""


def dtype_of(vtype: Any) -> str:
    """The wire's name for a value type: float, int, bool, str, enum, or json for anything else."""
    if vtype is float:
        return "float"
    if vtype is bool:
        return "bool"
    if vtype is int:
        return "int"
    if vtype is str:
        return "str"
    if isinstance(vtype, type) and issubclass(vtype, Enum):
        return "enum"
    return "json"


class Access(Flag):
    """Which of readable, publishing and writable a signal supports.

    `P` implies `R`: what a device publishes on its own schedule can also be
    read on demand, so a set with `P` but not `R` is refused wherever one is
    made -- combining flags, [parse][flyball.foundation.device.signal.Access.parse], a
    [SignalSpec][flyball.foundation.device.signal.SignalSpec]. On the wire the set is its
    lowercase letters: `"rp"`, `"w"`, `"rpw"`.
    """

    R = auto()
    """Readable: a GET returns a current value on demand."""
    P = auto()
    """Publishing: emitted in samples on the device's schedule; implies `R`."""
    W = auto()
    """Writable: accepts a demand; may be a controller's target."""
    RP = R | P
    RW = R | W
    RPW = R | P | W

    @classmethod
    def _missing_(cls, value: object) -> Any:
        member = super()._missing_(value)
        if isinstance(member, cls) and cls.P in member and cls.R not in member:
            # Flag caches the composite before handing it back; a refused one
            # must not be found ready-made next time.
            cls._value2member_map_.pop(value, None)
            cls.check(member)
        return member

    @classmethod
    def check(cls, access: Access) -> Access:
        """`access` back, or a ValueError when it has `P` without `R`."""
        if cls.P in access and cls.R not in access:
            raise ValueError(f"access {access!s}: P without R, but a publishing signal is readable")
        return access

    @classmethod
    def parse(cls, text: str) -> Access:
        """The set written as letters, in any order or case: `"rp"`, `"W"`."""
        access = cls(0)
        for letter in text:
            try:
                flag = cls[letter.upper()]
            except KeyError:
                raise ValueError(f"access {text!r}: {letter!r} is not one of r, p, w") from None
            if flag in access:
                raise ValueError(f"access {text!r} repeats {letter!r}")
            access |= flag
        return cls.check(access)

    def __str__(self) -> str:
        return "".join(letter for letter, flag in _LETTERS if flag in self)


_LETTERS = (("r", Access.R), ("p", Access.P), ("w", Access.W))
_ROLE_ACCESS: dict[Any, Access] = {}


def _excess(requested: Access, allowed: Access) -> str:
    """The letters of `requested` not in `allowed`; `""` if none.

    Plain int bit-work, not `Access.__invert__`: inverting a flag missing
    `P` (bare `R`, bare `W`) builds a `P`-without-`R` composite that
    `Access._missing_` refuses on sight, even mid-expression.
    """
    extra = requested.value & ~allowed.value
    return "".join(letter for letter, flag in _LETTERS if extra & flag.value)


def _check_band(name: str, field_name: str, band: tuple[Any, Any] | None) -> None:
    """Refuse an inverted or non-finite band, when both ends are plain numbers.

    A `limits` bound may be a [SignalRef][flyball.foundation.device.signal.SignalRef]
    rather than a number; those are resolved on the instance and are not checked here.
    """
    if band is None:
        return
    lo, hi = band
    if not (isinstance(lo, (int, float)) and isinstance(hi, (int, float))):
        return
    if not (math.isfinite(lo) and math.isfinite(hi)):
        raise ValueError(f"signal {name!r}: {field_name} {band!r}: must be finite")
    if lo > hi:
        raise ValueError(f"signal {name!r}: {field_name} {band!r}: inverted, low above high")


type Band = tuple[float, float]


class Limit(StrEnum):
    """Which end of its limits a demand sits on: the rig clamped it, or the driver railed it."""

    LOW = "low"
    HIGH = "high"

    @property
    def fraction(self) -> float:
        """The end as a fraction of the span: 0 for low, 1 for high."""
        return 1.0 if self is Limit.HIGH else 0.0


class Role(Enum):
    """What a signal is to its device: given, set, produced, or built from.

    - `DEMAND`: settable, with a current value (its readback) that updates;
      what a controller drives. Access `RPW`.
    - `OUTPUT`: produced, never set; a measurement, a derived value, a mode. `RP`.
    - `SETTING`: re-set by a command while the device runs, shown; not a
      scalar a controller could drive (a blend flow, a PWM frequency). `RP`.
    - `CONFIG`: effective at build, shown, never set at run time. `R`.
    - `INPUT`: another device's signal, bound by the rig to a role; not in
      the tree, but declared beside it so the schema can show what the
      device follows.
    """

    INPUT = "input"
    DEMAND = "demand"
    OUTPUT = "output"
    SETTING = "setting"
    CONFIG = "config"

    @property
    def access(self) -> Access:
        return _ROLE_ACCESS[self]


_ROLE_ACCESS.update({
    Role.INPUT: Access(0),
    Role.DEMAND: Access.RPW,
    Role.OUTPUT: Access.RP,
    Role.SETTING: Access.RP,
    Role.CONFIG: Access.R,
})


@dataclass(frozen=True, slots=True)
class Section:
    """A second grouping axis across a device's tree: `dry` / `wet` / `total`, `ch1` / `ch2`.

    A tag on the signal, never part of its address: `flows.dry` and
    `efforts.dry` share the section `dry`, so a UI can pivot the tree by
    section as well as by namespace. `axis` names what the sections are
    (`"line"`, `"channel"`) for a device with more than one grouping.
    """

    name: str
    label: str = ""
    axis: str = "line"

    def __post_init__(self) -> None:
        _check_segment(self.name)


type Bound = float | SignalRef
"""One end of `limits`: a number, or a reference to a signal whose current value it is."""


class SignalRef:
    """A reference to another signal of the same device, by path, resolved on the instance.

    What a descriptor is on a class (`limits=(0.0, dry_max_flow)`): the
    bound signal's `limits` resolve it to that signal's current value.
    """

    __slots__ = ("path",)

    def __init__(self, path: str) -> None:
        self.path = path

    def __repr__(self) -> str:
        return f"SignalRef({self.path!r})"


@dataclass(frozen=True, slots=True, kw_only=True)
class SignalSpec:
    """A device's declaration of one signal.

    From the driver; the rig file may restrict `access` and override the
    metadata, never add access the driver cannot honour -- except up to
    `ceiling`, when the driver names one.
    """

    name: str
    """One address segment: `"voltage"` under device `psu` is `psu.voltage`."""
    quantity: Quantity
    access: Access
    ceiling: Access | None = None
    """None (default): the rig file may only narrow `access`, as before. Set: the rig file may
    set `access` to anything from the driver's declared value up to and including `ceiling` --
    a driver opting a signal into being widened (an internal detail's `R` raised to `RP` for
    recording) without ever sanctioning access it did not name here."""
    role: Role = Role.OUTPUT
    section: Section | None = None
    tags: dict[str, str] = field(default_factory=dict)
    """Groupings across the tree, `{axis: name}`: the section's, plus any a driver or the rig
    file adds (`signals: {dry: {tags: {line: dry}}}`); a namespace's apply to all under it."""
    initial: Any = None
    """A value the signal has before anything reads or sets it: a mode's starting state."""
    vtype: Any = float
    """The type of a value: `float` for a measurement, an enum for a mode, a model for a
    structure. Anything pydantic can validate and describe."""
    shape: tuple[int, ...] = ()
    """The dimensions of a value: `()` a scalar. Only scalars are carried yet."""
    label: str = ""
    """The display text; `""` shows the titlecased name. The rig file may override it."""
    # read side (R / P)
    range: Band | None = None
    """The values a reading can plausibly take, for a gauge or an axis; None if unbounded."""
    precision: int | None = None
    """Decimal places worth showing; None if the driver has not said."""
    warn: Band | None = None
    """The band a value is normal inside (EPICS LOW/HIGH); outside it, a warning."""
    alarm: Band | None = None
    """The band a value is acceptable inside (EPICS LOLO/HIHI); outside it, an alarm."""
    poll_s: float | None = None
    """None: the enclosing node's; only meaningful with `P`."""
    stale_after: float | None = None
    """Seconds since the last reading beyond which a controller regulated from this signal
    treats it as untrustworthy: its demand is held rather than applied. None (default):
    never checked, today's behaviour."""
    # write side (W)
    limits: tuple[Bound, Bound] | None = None
    """What a demand is clamped to, in the signal's unit: numbers, or references to signals of
    the same device whose current values bound it (a config's max flow, an input's humidity)."""
    max_rate: Rate | None = None
    """How fast a demand may move, in the signal's unit per `Rate.per`: a demand that would
    move further than the elapsed time since the last commit allows is clamped to the
    largest step allowed, not refused. None (default): unlimited, today's behaviour."""

    def __post_init__(self) -> None:
        _check_segment(self.name)
        Access.check(self.access)
        if self.ceiling is not None:
            Access.check(self.ceiling)
            if missing := _excess(self.access, self.ceiling):
                raise ValueError(
                    f"signal {self.name!r}: ceiling {self.ceiling!s} excludes {missing},"
                    " part of its own declared access"
                )
        if self.shape != ():
            raise ValueError(f"signal {self.name!r}: shape {self.shape!r}: only scalars yet")
        _check_band(self.name, "range", self.range)
        _check_band(self.name, "warn", self.warn)
        _check_band(self.name, "alarm", self.alarm)
        _check_band(self.name, "limits", self.limits)
        if self.section is not None and self.section.axis not in self.tags:
            object.__setattr__(self, "tags", {self.section.axis: self.section.name, **self.tags})

    @property
    def dtype(self) -> str:
        """The wire's name for `vtype`, so a client can tell what arrives before it does."""
        return dtype_of(self.vtype)


@dataclass(frozen=True, slots=True, kw_only=True)
class NodeSpec:
    """A namespace inside a device: a sub-device or grouping. The driver declares the tree."""

    name: str
    children: tuple[NodeSpec | SignalSpec, ...]
    atomic: bool = False
    """Read (and written) as one Sample / one Demand."""
    label: str = ""
    poll_s: float | None = None
    """Inherited downwards; a child may override."""
    tags: dict[str, str] = field(default_factory=dict)
    """Applied to every signal under it at bind; a signal's own win."""

    def __post_init__(self) -> None:
        _check_segment(self.name)


def _check_segment(name: str) -> None:
    if not name or "." in name:
        raise ValueError(f"{name!r} is not an address segment: non-empty, no dots")


class Path(tuple[str, ...]):
    """A path inside a device: the segments of an address after the device's name.

    A value: hashable, made once at bind and owned by the bound object.
    `str()` joins the segments with dots (`"dry.humidity"`); `Path()` is
    the root, whose `str` is `""`. Strings exist only at the wire and in
    the rig file; [parse][flyball.foundation.device.signal.Path.parse] is the one way
    in.
    """

    __slots__ = ()

    @classmethod
    def parse(cls, text: str) -> Path:
        """`"dry.humidity"` as a path; `""` is the root.

        Raises:
            ValueError: An empty segment -- a leading, trailing or doubled dot.
        """
        if not text:
            return cls()
        segments = text.split(".")
        if "" in segments:
            raise ValueError(f"{text!r} is not a path: an empty segment")
        return cls(segments)

    def __truediv__(self, segment: str) -> Path:
        _check_segment(segment)
        return Path((*self, segment))

    @property
    def parent(self) -> Path:
        """The path above; the root's parent is the root."""
        return Path(self[:-1])

    @property
    def name(self) -> str:
        """The last segment; `""` for the root."""
        return self[-1] if self else ""

    def is_under(self, other: Path) -> bool:
        """Whether `other` is this path or above it."""
        return self[: len(other)] == other

    def __str__(self) -> str:
        return ".".join(self)

    def __repr__(self) -> str:
        return f"Path({str(self)!r})"


class AddressNotFoundError(NotFoundError):
    """An address did not resolve: which segment failed, and under what."""

    def __init__(self, address: str, segment: str, under: str | None) -> None:
        where = f"no device {segment!r}" if under is None else f"no {segment!r} under {under}"
        super().__init__(f"Address {address!r} not found: {where}")


@dataclass(eq=False, slots=True)
class Node:
    """A bound namespace, or the device itself (`spec` None, address the device's name).

    One object per namespace per device, made by `Device.bind`; equality is
    identity. `signals` and `children` are the leaves and namespaces
    directly under it, by their own name.
    """

    spec: NodeSpec | None
    device: Device
    parent: Node | None
    address: str
    """`"hum_sensors.dry"`; the device name for the root."""
    path: Path
    """The address relative to the device: `dry`; the root's is empty."""
    signals: dict[str, Signal] = field(default_factory=dict)
    children: dict[str, Node] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"Node({self.address})"

    @property
    def name(self) -> str:
        return self.device.name if self.spec is None else self.spec.name

    @property
    def label(self) -> str:
        return (self.device.label or "") if self.spec is None else self.spec.label

    @property
    def atomic(self) -> bool:
        """Read (and written) as one sample: the namespace says so, or the device for its root."""
        return self.device.atomic if self.spec is None else self.spec.atomic

    @property
    def poll_s(self) -> float | None:
        """This node's period: its own, else the parent's, else the device's."""
        if self.spec is not None and self.spec.poll_s is not None:
            return self.spec.poll_s
        return self.device.poll_s if self.parent is None else self.parent.poll_s

    def walk(self) -> Iterator[Signal]:
        """Every signal under this node, depth-first; each carries its address."""
        yield from self.signals.values()
        for child in self.children.values():
            yield from child.walk()

    def descendants(self) -> Iterator[Node]:
        """Every namespace under this node, depth-first, parents before children."""
        for child in self.children.values():
            yield child
            yield from child.descendants()

    def find(self, relative: str) -> Signal | Node:
        """The signal or namespace at a dotted path under this node; `""` is the node itself.

        The one rule for a relative name -- a wire key of a sample or a
        demand, the rest of an address after the device -- applied at the
        boundary; below it everything carries the bound objects.

        Raises:
            AddressNotFoundError: Naming the segment that failed and what
                it was looked for under.
        """
        if not relative:
            return self
        address = f"{self.address}.{relative}"
        node = self
        try:
            path = Path.parse(relative)
        except ValueError:  # a trailing or doubled dot names nothing
            raise AddressNotFoundError(address, "", node.address) from None
        for i, segment in enumerate(path):
            if (signal := node.signals.get(segment)) is not None:
                if i + 1 < len(path):
                    raise AddressNotFoundError(address, path[i + 1], signal.address)
                return signal
            if (child := node.children.get(segment)) is None:
                raise AddressNotFoundError(address, segment, node.address)
            node = child
        return node

    def contains(self, item: Signal | Node) -> bool:
        """Whether `item` is this node or lies under it: the walk up its parents reaches here."""
        node: Node | None = item.node if isinstance(item, Signal) else item
        while node is not None:
            if node is self:
                return True
            node = node.parent
        return False

    def relative(self, signal: Signal) -> str:
        """`signal`'s dotted path relative to this node: the wire's key for it in a sample.

        `"dry.humidity"` from the device root, `"humidity"` from
        `hum_sensors.dry`.

        Raises:
            ValueError: `signal` is not under this node.
        """
        if not self.contains(signal):
            raise ValueError(f"'{signal.address}' is not under '{self.address}'")
        return str(Path(signal.path[len(self.path) :]))

    def override(self, **changes: Any) -> None:
        """Replace fields of the spec in place; the bound object keeps its identity."""
        if self.spec is None:
            raise ValueError(f"Node '{self.address}' is a device root; override the device")
        self.spec = replace(self.spec, **changes)


@dataclass(eq=False, slots=True)
class Signal:
    """A bound signal: the spec, the node it hangs off, and its address.

    One object per signal per device, made by `Device.bind`; equality is
    identity. `access` is the set after any rig-file restriction; `spec.access`
    stays what the driver declared.
    """

    spec: SignalSpec
    node: Node
    address: str
    """`"hum_sensors.dry.humidity"`."""
    path: Path
    """The address relative to the device: `dry.humidity`."""
    access: Access
    at_limit: Limit | None = None
    """What the driver says of the last demand on it: railed low or high, or neither. Set in
    `commit`; the rig puts it on the demand's write state."""

    def __repr__(self) -> str:
        return f"Signal({self.address} [{self.access}])"

    @property
    def role(self) -> Role:
        return self.spec.role

    @property
    def tags(self) -> dict[str, str]:
        return self.spec.tags

    @property
    def device(self) -> Device:
        return self.node.device

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def quantity(self) -> Quantity:
        return self.spec.quantity

    @property
    def unit(self) -> Unit:
        return self.spec.quantity.unit

    @property
    def label(self) -> str:
        return self.spec.label

    @property
    def range(self) -> Band | None:
        """What a gauge or an axis spans: the signal's own, else its limits, else the unit's scale.

        None if none of them say.
        """
        return self.spec.range or self.limits or self.unit.scale

    @property
    def limits(self) -> Band | None:
        """The effective limits now: a referenced signal's current value stands for it.

        A reference names a signal of the device by path, or one of its
        inputs by role (the bound source's newest value, or the input's
        default). None if there are none, or a reference has no value yet.
        """
        if (limits := self.spec.limits) is None:
            return None
        resolved: list[float] = []
        for bound in limits:
            if isinstance(bound, SignalRef):
                value = self.node.device.referenced(bound.path)
                if value is None:
                    return None
                resolved.append(float(value))
            else:
                resolved.append(bound)
        return (resolved[0], resolved[1])

    @property
    def pending(self) -> float | None:
        """The demand recorded on this signal since the last commit, if any."""
        return self.node.device.pending.get(self)

    @property
    def poll_s(self) -> float | None:
        """This signal's period: its own, else inherited down the tree from the device."""
        return self.node.poll_s if self.spec.poll_s is None else self.spec.poll_s

    @property
    def router(self) -> Router:
        """Where this signal's values live: the rig's router once added, the device's own before."""
        return self.node.device.router

    @property
    def reading(self) -> Reading | None:
        """The newest reading on this signal, from the router; None before the first."""
        return self.router.reading(self)

    @property
    def value(self) -> Value:
        """The newest value on this signal.

        Raises:
            NotReadyError: Nothing has been read on it yet.
        """
        return self.router.value(self)

    def push(self, value: Value, time_ns: int | None = None) -> None:
        """Put `value` on this signal now (or at `time_ns`): a sample of one reading, delivered.

        Inside the device's `batch()` it joins the batch instead.
        """
        self.node.device.push_one(self, value, time_ns)

    def override(self, **changes: Any) -> None:
        """Replace metadata fields of the spec in place; the bound object keeps its identity."""
        self.spec = replace(self.spec, **changes)

    def restrict(self, access: Access) -> None:
        """Set `access` to a subset of what the driver declared, or up to its `ceiling`.

        Without a ceiling this only narrows, as before; with one, the rig
        file may widen up to it, never beyond.
        """
        allowed = self.spec.access if self.spec.ceiling is None else self.spec.ceiling
        if added := _excess(access, allowed):
            raise ValueError(
                f"Signal '{self.address}' cannot add access {added}:"
                f" the driver allows at most {allowed!s}"
            )
        self.access = Access.check(access)

    def write_state(self, value: float) -> WriteState:
        """What committing `value` reports: at a limit when it sits on one (the rig clamped)."""
        at_limit: Limit | None = None
        if (limits := self.limits) is not None:
            if value <= limits[0]:
                at_limit = Limit.LOW
            elif value >= limits[1]:
                at_limit = Limit.HIGH
        return WriteState(value=value, requested=None, at_limit=at_limit, controller=None)


@dataclass(frozen=True, slots=True)
class Reading:
    """One value on one signal at one instant. On a demand, the newest is its readback.

    `requested`, `at_limit` and `controller` are the write record folded in: set only on a
    demand's reading, by the rig, when this is what a commit just produced -- the old
    `WriteState`, riding along instead of a stream of its own.
    """

    signal: Signal
    time_ns: int
    value: Value
    requested: float | None = None
    """What was asked for, when the clamp changed it."""
    at_limit: Limit | None = None
    controller: str | None = None
    """The controller driving the signal, if any."""

    @property
    def seconds(self) -> float:
        return self.time_ns / 1e9


@dataclass(frozen=True, slots=True)
class Sample:
    """Signals under one node read at one instant: the node's declared tree is the message type.

    `values` are keyed by the bound [Signal][flyball.foundation.device.signal.Signal]
    objects, so nothing that handles a sample looks a name up; the wire
    gets `node.address` (`"hum_sensors.dry"`, `"furnace"`) and
    [by_name][flyball.foundation.device.signal.Sample.by_name] -- flat, dotted paths
    relative to the node, never nested. Any readable signal under the node
    may appear and any may be missing -- not read at this instant -- but
    there is always at least one. Only what publishes leaves the rig.
    """

    node: Node
    time_ns: int
    values: Mapping[Signal, Value]

    @property
    def seconds(self) -> float:
        return self.time_ns / 1e9

    def readings(self) -> Iterator[Reading]:
        """One [Reading][flyball.foundation.device.signal.Reading] per value, its bound signal."""
        time_ns = self.time_ns
        return (Reading(signal, time_ns, value) for signal, value in self.values.items())

    def by_name(self, relative_to: Node | None = None) -> dict[str, Value]:
        """The wire form: each value by its dotted path relative to `relative_to` (default `node`).

        Raises:
            ValueError: A signal is not under `relative_to`.
        """
        node = self.node if relative_to is None else relative_to
        return {node.relative(signal): value for signal, value in self.values.items()}

    def published(self) -> Sample | None:
        """Without the values on non-publishing signals: itself if none, None if nothing is left."""
        kept = {s: v for s, v in self.values.items() if Access.P in s.access}
        if len(kept) == len(self.values):
            return self
        return Sample(self.node, self.time_ns, kept) if kept else None

    def under(self, node: Node) -> Sample | None:
        """The values under `node`, as a sample on it; None if there are none.

        `node` may be this sample's, above it, or below it; anything on the
        same device. The keys are the same objects: only the node changes.
        """
        if node is self.node:
            return self
        kept = {s: v for s, v in self.values.items() if node.contains(s)}
        return Sample(node, self.time_ns, kept) if kept else None


@dataclass(frozen=True, slots=True)
class Demand:
    """One or more values put on W signals under one node at one instant: a Sample in reverse.

    A rig-level object: the rig validates it whole, then fans it out to
    `Device.apply` one signal at a time. `values` are keyed by the bound
    signals, as a Sample's are, in each signal's unit; the rig resolves the
    wire's relative names once, at entry, through `Node.find`.
    """

    node: Node
    time_ns: int
    values: Mapping[Signal, float]


@dataclass(frozen=True, slots=True, kw_only=True)
class WriteState:
    """What a device reports about one W signal after a demand (Tango's w_value + status)."""

    value: float | None
    """What was last set, after limits."""
    requested: float | None = None
    """What was asked for, if it differed."""
    at_limit: Limit | None = None
    controller: str | None = None
    """The controller driving it, if any; it refuses manual demands."""
