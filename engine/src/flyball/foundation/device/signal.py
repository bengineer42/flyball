"""Signals: what a device declares, what it is bound to, and what moves on them.

A driver declares its tree once, as [SignalSpec][flyball.foundation.device.signal.SignalSpec]
leaves grouped by [NodeSpec][flyball.foundation.device.signal.NodeSpec] namespaces, each
leaf with an [Access][flyball.foundation.device.signal.Access] set. Binding the tree to a
device (`Device.bind`) makes one [Node][flyball.foundation.device.signal.Node] or
[Signal][flyball.foundation.device.signal.Signal] per spec, each carrying its address; they
are identity-hashed and created once, so a
[Reading][flyball.foundation.device.signal.Reading], a
[Sample][flyball.foundation.device.signal.Sample]
or a [Write][flyball.foundation.device.signal.Write] holds a reference and nothing on the
hot path looks a name up. Addresses are parsed once, at the boundary.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum, Flag, StrEnum, auto
from typing import TYPE_CHECKING, Any

from ..errors import NotFoundError, NotReadyError, UnachievableError
from ..quantities import Unit
from ..quantities.quantity import Quantity
from ..time.clock import Rate
from .novalue import NoValue, OnNoValue, Quality, Railed, Readback, invalid
from .state import Code

if TYPE_CHECKING:
    from ..router.router import Router
    from .descriptors import BoundInput
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
    """Which of readable, published and writable a signal supports.

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
            raise ValueError(f"access {access!s}: P without R, but a published signal is readable")
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


type Bounds = tuple[float, float]


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
      the only thing a controller drives (its output). Access `RPW`.
    - `READOUT`: produced by the device, never written from outside; a
      measurement, a derived value, a mode. `RP`.
    - `SETTING`: re-set by a command while the device runs, shown; not a
      scalar a controller could drive (a blend flow, a PWM frequency). `RP`.
    - `CONFIG`: effective at build, shown, never set at run time. `R`.

    An input (another device's signal, bound by the rig) is not a role: it is
    not in the device's tree. See [Input][flyball.foundation.device.descriptors.Input].
    """

    DEMAND = "demand"
    READOUT = "readout"
    SETTING = "setting"
    CONFIG = "config"

    @property
    def access(self) -> Access:
        return _ROLE_ACCESS[self]


_ROLE_ACCESS.update({
    Role.DEMAND: Access.RPW,
    Role.READOUT: Access.RP,
    Role.SETTING: Access.RP,
    Role.CONFIG: Access.R,
})


type Bound = float | SignalRef
"""One end of `limits`: a number, or a reference to a signal whose current value it is."""


class SignalRef:
    """A reference to another signal of the same device, by path, resolved on the instance.

    What a descriptor is on a class (`limits=(0.0, dry_max_flow)`): when the
    device binds, each bound signal resolves it once to the signal or input
    it names, and its `limits` read that object's current value.
    """

    __slots__ = ("path",)

    def __init__(self, path: str) -> None:
        self.path = path

    def __repr__(self) -> str:
        return f"SignalRef({self.path!r})"


@dataclass(frozen=True, slots=True, kw_only=True)
class SignalSpec:
    """A device's declaration of one signal.

    From the driver; the rig file may restrict `access` and set the
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
    role: Role = Role.READOUT
    tags: dict[str, str] = field(default_factory=dict)
    """Groupings across the tree, `{axis: name}` (`{"line": "dry"}`): what the driver declares,
    plus any the rig file adds (`signals: {dry: {tags: {line: dry}}}`); a namespace's apply to
    all under it. Never part of the address: `flows.dry` and `efforts.dry` share `line: dry`."""
    initial: Any = None
    """A value the signal has before anything reads or sets it: a mode's starting state."""
    vtype: Any = float
    """The type of a value: `float` for a measurement, an enum for a mode, a model for a
    structure. Anything pydantic can validate and describe."""
    shape: tuple[int, ...] = ()
    """The dimensions of a value: `()` a scalar. Only scalars are carried yet."""
    label: str = ""
    """The display text; `""` shows the titlecased name. The rig file may set it."""
    # read side (R / P)
    range: Bounds | None = None
    """The values a reading can plausibly take, for a gauge or an axis; None if unbounded."""
    precision: int | None = None
    """Decimal places worth showing; None if the driver has not said."""
    warning: Bounds | None = None
    """The band a value is normal inside (EPICS LOW/HIGH); outside it, a warning."""
    alarm: Bounds | None = None
    """The band a value is acceptable inside (EPICS LOLO/HIHI); outside it, an alarm."""
    poll_s: float | None = None
    """None: the enclosing node's; only meaningful with `P`."""
    stale_after_s: float | None = None
    """Seconds since the last reading beyond which a controller regulated from this signal
    treats it as untrustworthy: its demand is held rather than applied. None (default):
    never checked, today's behaviour."""
    # write side (W)
    limits: tuple[Bound, Bound] | None = None
    """What a demand is clamped to, in the signal's unit: numbers, or references to signals of
    the same device whose current values bound it (a config's max flow, an input's humidity).
    A demand while a referenced signal has no value yet, or a non-finite one (NaN, inf), is
    refused, never passed unclamped."""
    max_rate: Rate | None = None
    """How fast a demand may move, in the signal's unit per `Rate.per`: a demand that would
    move further than the elapsed time since the last commit allows is clamped to the
    largest step allowed, not refused. None (default): unlimited, today's behaviour."""
    readback: Readback = Readback.ECHO
    """A demand's: `echo` (default) when its reading is the value the rig committed, so it is
    `stale(write_failed)` while its device's writes fail; `sensed` when the driver reads it back
    from the hardware, so it is a measurement like any readout's."""
    on_no_value: OnNoValue | None = None
    """A banded signal's: whether a fault with no value (`invalid`) raises `band_unknown` after
    its grace. None (default): `fire` with an `alarm` band, `ignore` with only `warning`."""

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
        _check_band(self.name, "warning", self.warning)
        _check_band(self.name, "alarm", self.alarm)
        _check_band(self.name, "limits", self.limits)

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
    """Read (and written) as one Sample / one Write."""
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

    def set_meta(self, **changes: Any) -> None:
        """Replace fields of the spec in place; the bound object keeps its identity."""
        if self.spec is None:
            raise ValueError(f"Node '{self.address}' is a device root; set the device's own")
        self.spec = replace(self.spec, **changes)


type Followed = float | Signal | BoundInput
"""One end of a bound signal's limits: a number, or the signal or input a `SignalRef` named."""


def _value_of(bound: Followed) -> float | None:
    """A limit's current value: the number, or what it follows now; None if that is not known.

    A followed signal whose newest reading is a no-value is not known: the limit fails closed.
    """
    if isinstance(bound, (int, float)):
        return bound
    if isinstance(bound, Signal):
        reading = bound.router.reading(bound)
        return None if reading is None else _finite(reading.value)
    try:
        return _finite(bound.value)
    except NotReadyError:
        return None


def _name(bound: Followed) -> str:
    """How a followed limit is named in a refusal: its path, or the input's role."""
    if isinstance(bound, (int, float)):
        return str(bound)
    return str(bound.path) if isinstance(bound, Signal) else bound.input.name


def _shown(limits: tuple[Bound, Bound]) -> str:
    """A driver's limits for a message: numbers as numbers, a reference by its path."""
    return (
        "("
        + ", ".join(
            str(bound) if isinstance(bound, (int, float)) else repr(bound.path) for bound in limits
        )
        + ")"
    )


def _finite(value: Any) -> float | None:
    """A referenced bound as a number, or None: not known -- no value, or not a finite one."""
    if value is None or isinstance(value, NoValue):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class LimitNotKnownError(NotReadyError):
    """A demand was refused: a limit follows a signal that has no value yet, or a non-finite one.

    Fail closed: an unresolved limit never lets a demand through unclamped
    -- nor clamped to the other end, as `min(max(v, lo), nan)` would be.
    The demand succeeds once the bound reads a finite value.
    """

    def __init__(self, address: str, unknown: list[str]) -> None:
        self.address = address
        self.unknown = unknown
        which = ", ".join(repr(path) for path in unknown) or "a referenced signal"
        super().__init__(
            f"Demand on '{address}' refused: its limit follows {which}, which has no value yet, "
            "or not a finite one"
        )


class LimitsInvertedError(UnachievableError):
    """A demand was refused: the effective limits, as they resolve now, have low above high.

    A limit that follows a signal (a dry supply read wetter than the wet
    one), or a rig file's narrowing that the driver's live band has moved
    clear of, leaves no value a demand could be clamped to. Refused rather
    than held at either end.
    """

    def __init__(self, address: str, limits: Bounds) -> None:
        self.address = address
        self.limits = limits
        self.unknown: list[str] = []
        super().__init__(
            f"Demand on '{address}' refused: its limits are inverted now,"
            f" low {limits[0]:g} above high {limits[1]:g}"
        )


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
    narrowed: Bounds | None = None
    """The rig file's `limits`: a band a demand is held inside as well as the driver's, never
    instead of them. Set through [narrow][flyball.foundation.device.signal.Signal.narrow]."""
    _bounds: tuple[Followed, Followed] | None = field(default=None, repr=False)
    """`spec.limits` with each `SignalRef` resolved to what it follows; see `bind_limits`."""
    _bounds_for: SignalSpec | None = field(default=None, repr=False)
    """The spec `_bounds` came from: `set_meta` replaces the spec, and they resolve again."""

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
    def range(self) -> Bounds | None:
        """What a gauge or an axis spans: the signal's own, else its limits, else the unit's scale.

        None if none of them say.
        """
        return self.spec.range or self.limits or self.unit.scale

    @property
    def limits(self) -> Bounds | None:
        """The effective limits now: the driver's, intersected with the rig file's narrowing.

        A reference in the driver's names a signal of the device by path, or
        one of its inputs by role (the bound source's newest value, or the
        input's default); its current value stands for it. None if there are
        none, if a reference has no value yet or a non-finite one (NaN, inf),
        or if the band is inverted now -- for display; a demand goes through
        [clamp][flyball.foundation.device.signal.Signal.clamp], which refuses
        it in the last two cases rather than pass it unclamped.
        """
        band, unknown = self._resolved()
        if unknown or band is None or band[0] > band[1]:
            return None
        return band

    def _resolved(self) -> tuple[Bounds | None, list[str]]:
        """The effective band, maybe inverted, and the bounds that are not known now."""
        narrowed = self.narrowed
        if (bounds := self.bind_limits()) is None:
            return narrowed, []
        low, high = _value_of(bounds[0]), _value_of(bounds[1])
        if low is None or high is None:
            return None, [_name(bound) for bound in bounds if _value_of(bound) is None]
        if narrowed is not None:
            low, high = max(low, narrowed[0]), min(high, narrowed[1])
        return (low, high), []

    def narrow(self, band: Bounds | None) -> None:
        """Hold demands inside `band` as well as the driver's limits; None drops the narrowing.

        The rig file's `limits`. The effective limits are the intersection,
        worked out at every clamp: a driver bound that follows a signal is
        intersected with its value then. A rig file may only narrow.

        Raises:
            ValueError: `band` is inverted or not finite, or reaches past an
                end of the driver's limits that is a number.
        """
        if band is not None:
            _check_band(self.name, "limits", band)
            if (declared := self.spec.limits) is not None:
                low, high = declared
                if (isinstance(low, (int, float)) and band[0] < low) or (
                    isinstance(high, (int, float)) and band[1] > high
                ):
                    raise ValueError(
                        f"'{self.address}': limits {tuple(band)!r} reach outside the driver's"
                        f" {_shown(declared)}; a rig file may only narrow them"
                    )
        self.narrowed = band

    def bind_limits(self) -> tuple[Followed, Followed] | None:
        """The spec's limits with each reference resolved to the signal or input it follows.

        Resolved once, when the device binds, and again only if `set_meta`
        replaces the spec; reading a limit then costs no lookup by name.
        None if the signal has no limits.

        Raises:
            ValueError: A reference names neither a signal of this device nor
                one of its inputs.
        """
        if self._bounds_for is not self.spec:
            limits = self.spec.limits
            self._bounds = (
                None if limits is None else (self._follow(limits[0]), self._follow(limits[1]))
            )
            self._bounds_for = self.spec
        return self._bounds

    def _follow(self, bound: Bound) -> Followed:
        if not isinstance(bound, SignalRef):
            return bound
        device = self.node.device
        if (signal := device.signals.get(bound.path)) is not None:
            return signal
        if (input_ := device.INPUTS.get(bound.path)) is not None:
            return input_.on(device)
        raise ValueError(
            f"'{self.address}': a limit follows {bound.path!r}, which is neither a signal"
            f" nor an input of {device.name!r}"
        )

    def clamp(self, value: float) -> float:
        """`value` held inside the effective limits; unchanged for a signal without limits.

        The effective limits are the driver's intersected with the rig
        file's narrowing, resolved now. Fails closed: when a bound follows a
        signal with no value yet, or a non-finite one (NaN, inf), the demand
        is refused rather than passed through unclamped -- even when the
        other end is a number, since the unknown end is the one that matters
        (a supply's humidity, a max flow read from the device).

        Raises:
            LimitNotKnownError: A bound follows a signal that has no value yet,
                or a non-finite one.
            LimitsInvertedError: The limits resolve with low above high.
        """
        band, unknown = self._resolved()
        if unknown:
            raise LimitNotKnownError(self.address, unknown)
        if band is None:
            return value
        if band[0] > band[1]:
            raise LimitsInvertedError(self.address, band)
        return min(max(value, band[0]), band[1])

    @property
    def staged(self) -> float | None:
        """The value written to this signal and staged since the last commit, if any."""
        return self.node.device.staged.get(self)

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

    def set_meta(self, **changes: Any) -> None:
        """Replace metadata fields of the spec in place; the bound object keeps its identity.

        A `warning` or `alarm` band removed takes its condition with it
        (`band_warning`, `band_alarm`; `band_unknown` with the last band),
        cleared at once: there is no band left for a reading to come back inside.
        """
        before, self.spec = self.spec, replace(self.spec, **changes)
        for band, code in (("warning", Code.BAND_WARNING), ("alarm", Code.BAND_ALARM)):
            if getattr(before, band) is not None and getattr(self.spec, band) is None:
                self.node.device.conditions.clear(self, code, message=f"{band} band removed")
        if self.spec.warning is None and self.spec.alarm is None:
            self.node.device.conditions.clear(self, Code.BAND_UNKNOWN, message="no band left")

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
    """The value, or a [NoValue][flyball.foundation.device.novalue.NoValue]: test `usable`."""
    requested: float | None = None
    """What was asked for, when the clamp changed it."""
    at_limit: Limit | None = None
    """The caveat `at_limit`: the rig clamped this demand to an end of its limits, or the driver
    reported the value railed at an end of what it can read."""
    controller: str | None = None
    """The controller driving the signal, if any."""

    @property
    def seconds(self) -> float:
        return self.time_ns / 1e9

    @property
    def usable(self) -> bool:
        """Whether it has a value a consumer may use: not a no-value."""
        return not isinstance(self.value, NoValue)

    @property
    def quality(self) -> Quality:
        """`ok`, or the no-value's quality."""
        value = self.value
        return value.quality if isinstance(value, NoValue) else Quality.OK

    @property
    def reason(self) -> str:
        """The no-value's reason; `""` for a usable value."""
        value = self.value
        return value.reason if isinstance(value, NoValue) else ""


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

    A value may be a [NoValue][flyball.foundation.device.novalue.NoValue]: read, and none.
    `marks` are the caveat `at_limit` of the values that carry it, sparse; a driver yields
    `railed(value, "high")` and [normalised][flyball.foundation.device.signal.normalised]
    moves it here.
    """

    node: Node
    time_ns: int
    values: Mapping[Signal, Value]
    marks: Mapping[Signal, Limit] = field(default_factory=dict[Signal, Limit])

    @property
    def seconds(self) -> float:
        return self.time_ns / 1e9

    def readings(self) -> Iterator[Reading]:
        """One [Reading][flyball.foundation.device.signal.Reading] per value, its bound signal."""
        time_ns = self.time_ns
        marks = self.marks
        if not marks:
            return (Reading(signal, time_ns, value) for signal, value in self.values.items())
        return (
            Reading(signal, time_ns, value, at_limit=marks.get(signal))
            for signal, value in self.values.items()
        )

    def by_name(self, relative_to: Node | None = None) -> dict[str, Value]:
        """The wire form: each value by its dotted path relative to `relative_to` (default `node`).

        Raises:
            ValueError: A signal is not under `relative_to`.
        """
        node = self.node if relative_to is None else relative_to
        return {node.relative(signal): value for signal, value in self.values.items()}

    def published(self) -> Sample | None:
        """Without the values on non-published signals: itself if none, None if nothing is left."""
        kept = {s: v for s, v in self.values.items() if Access.P in s.access}
        if len(kept) == len(self.values):
            return self
        return Sample(self.node, self.time_ns, kept, _kept(self.marks, kept)) if kept else None

    def under(self, node: Node) -> Sample | None:
        """The values under `node`, as a sample on it; None if there are none.

        `node` may be this sample's, above it, or below it; anything on the
        same device. The keys are the same objects: only the node changes.
        """
        if node is self.node:
            return self
        kept = {s: v for s, v in self.values.items() if node.contains(s)}
        return Sample(node, self.time_ns, kept, _kept(self.marks, kept)) if kept else None


def _kept(marks: Mapping[Signal, Limit], kept: Mapping[Signal, Value]) -> Mapping[Signal, Limit]:
    """The marks of the values `kept`: what a sample cut down to them carries."""
    return {s: m for s, m in marks.items() if s in kept} if marks else marks


def _gate(value: Value) -> tuple[Value, Limit | None, bool]:
    """One value through the gate: the value (or a no-value), its mark, whether it changed."""
    if value is None:
        return invalid("no value"), None, True
    if isinstance(value, float) and not math.isfinite(value):
        return invalid("not finite"), None, True
    if isinstance(value, Railed):
        inner, _, _ = _gate(value.value)
        if isinstance(inner, NoValue):
            return inner, None, True
        return inner, Limit(value.side), True
    return value, None, False


def normalised(sample: Sample) -> Sample:
    """`sample` through the value gate: itself when every value is already a value or a no-value.

    `None`, NaN and the infinities become `invalid` no-values (`"no value"`, `"not finite"`): a
    number nobody can use is not passed on as one. A `railed(value, side)` becomes the value,
    with its side in `marks`.
    """
    values: dict[Signal, Value] | None = None
    marks: dict[Signal, Limit] | None = None
    for signal, value in sample.values.items():
        gated, mark, changed = _gate(value)
        if not changed:
            continue
        if values is None:
            values, marks = dict(sample.values), dict(sample.marks)
        assert marks is not None
        values[signal] = gated
        if mark is None:
            marks.pop(signal, None)
        else:
            marks[signal] = mark
    if values is None:
        return sample
    assert marks is not None
    return Sample(sample.node, sample.time_ns, values, marks)


@dataclass(frozen=True, slots=True)
class Write:
    """One or more values written to W signals under one node at one instant: a Sample in reverse.

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
