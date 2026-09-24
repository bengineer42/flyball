"""Input bindings: what an `inputs:` entry is once the rig has resolved it.

A device's input (`inputs: {dry: hum_sensors.dry.humidity}`, or `{dry: 36.5}`) is
an [InputBinding][flyball.foundation.device.binding.InputBinding]: the rig
resolves the address once, at build, to the source signal (or namespace), or
takes the number as a constant, and the holder reads the binding from then on --
its value, its quality, how old it is, and what it follows. A limit that
follows the input holds the same object, and so may anything else that
follows a signal: a controller, a program's `settle` step. Nothing below the
rig parses the address again.

Nothing substitutes a value. A constant has one from build; an address is
`pending` until its source's first reading, and has no value while the
source's newest reading has none (`invalid`, `stale`, `not_applicable`):
[value][flyball.foundation.device.binding.InputBinding.value] raises. A
device output computed from an input carries the input's quality: the driver
pushes the source's no-value (`NoValueError.no_value`) on the outputs it could
not compute, and [values_of][flyball.foundation.device.binding.values_of]
picks the one that matters when several inputs have none.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..errors import NotReadyError
from ..quantities import Unit
from .novalue import NoValue, NoValueError, Quality, Reason
from .signal import Access, Limit, Node, Reading, Signal, Value

if TYPE_CHECKING:
    from .descriptors import Input

log = logging.getLogger("flyball.inputs")

type Followable = Signal | Node | float
"""What an input may follow: a signal, a whole namespace, or a number."""


@dataclass(frozen=True, slots=True)
class InputState:
    """An input now, in one call: what `InputBinding.state` returns."""

    value: Value | None
    """The value, or None when there is none (`quality` says why)."""
    quality: Quality
    reason: str
    """The source's reason for having no value; `""` when it has one, or for `pending`."""
    at_limit: Limit | None
    """The caveat `at_limit` on the source's reading, if any."""
    age_s: float | None
    """Seconds since the rig received the newest reading with a value, on the rig's clock;
    None for a constant, and before a first reading."""
    follows: str | None
    """The address it follows; None for a constant or an unbound input."""
    constant: float | None
    """The number, for a constant binding."""
    unit: str | None
    """The source's unit symbol (a constant's: the declared input's), if known."""


class InputBinding:
    """One input of a device (or of anything else that follows a signal), resolved by the rig.

    Made unbound; [Rig.bind][flyball.rig.rig.Rig.bind] points it at a source
    signal or namespace, or at a constant, and keeps the identity: a limit
    that follows the input resolved to this object when its device was added,
    before the rig bound it. `owner` is what holds it (a `Device`, a
    controller, a program step); `name` the input's name on it (`"dry"`).

    Reads go to the source's newest reading, on the rig's router; nothing is
    cached here. Watchers ([watch][flyball.foundation.device.binding.InputBinding.watch])
    are called on the delivery thread, under the rig's lock, with each new
    reading on the source -- a change of quality always arrives as a reading.
    """

    __slots__ = ("_constant", "_follows", "_watchers", "declared", "name", "owner", "where")

    def __init__(
        self, owner: object, name: str, declared: Input | None = None, *, where: str = ""
    ) -> None:
        self.owner = owner
        self.name = name
        self.declared: Any = declared  # not a class-level Descriptor annotation: see Namespace
        self.where = where or f"{getattr(owner, 'name', owner)}.inputs.{name}"
        """How the binding is named in a message: `blender.inputs.dry`."""
        self._follows: Signal | Node | None = None
        self._constant: float | None = None
        self._watchers: list[Callable[[InputBinding], None]] = []

    def __repr__(self) -> str:
        return f"InputBinding({self.where} <- {self.spelled!r})"

    # region What it follows

    @property
    def bound(self) -> bool:
        """Whether the rig has given it a source or a number."""
        return self._follows is not None or self._constant is not None

    @property
    def follows(self) -> Signal | Node | None:
        """The signal or namespace it follows; None for a constant, or before it is bound."""
        return self._follows

    @property
    def signal(self) -> Signal | None:
        """The source when it is one signal; None otherwise."""
        return self._follows if isinstance(self._follows, Signal) else None

    @property
    def constant(self) -> float | None:
        """The number, for a constant binding (`inputs: {dry: 36.5}`)."""
        return self._constant

    @property
    def address(self) -> str | None:
        """The source's address; None for a constant or an unbound input."""
        return None if self._follows is None else self._follows.address

    @property
    def spelled(self) -> str | float | None:
        """As the rig file writes it: the address, or the number; None while unbound."""
        return self._constant if self._follows is None else self._follows.address

    @property
    def unit(self) -> Unit | None:
        """The source signal's unit; a constant's is the declared input's, if it declares one."""
        if (signal := self.signal) is not None:
            return signal.unit
        if self.declared is not None:
            return self.declared.quantity.unit
        return None

    def attach(self, source: Signal | Node | float) -> None:
        """Point it at `source`. The rig's: [Rig.bind][flyball.rig.rig.Rig.bind] checks first."""
        if isinstance(source, (Signal, Node)):
            self._follows, self._constant = source, None
        else:
            self._follows, self._constant = None, float(source)

    def detach(self) -> None:
        """Back to unbound: its source went away. Watchers stay attached."""
        self._follows = self._constant = None

    # endregion

    # region What it reads now

    @property
    def reading(self) -> Reading | None:
        """The source signal's newest reading; None for a constant, a namespace, or none yet."""
        signal = self.signal
        return None if signal is None else signal.router.reading(signal)

    @property
    def value(self) -> Value:
        """The constant, or the source's newest value.

        Raises:
            NotReadyError: Unbound, or its source has not been read yet (`pending`).
            NoValueError: The source's newest reading has no value: its `no_value`
                is the source's, quality and reason, for an output computed from it.
            TypeError: It follows a whole namespace: read the node's sample instead.
        """
        if self._constant is not None:
            return self._constant
        source = self._follows
        if source is None:
            raise NotReadyError(f"{self.where}: not bound")
        if isinstance(source, Node):
            raise TypeError(f"{self.where} follows the namespace '{source.address}', not a signal")
        reading = source.router.reading(source)
        if reading is None:
            raise NotReadyError(f"{self.where}: '{source.address}' has not been read yet")
        value = reading.value
        if isinstance(value, NoValue):
            raise NoValueError(source.address, value)
        return value

    @property
    def quality(self) -> Quality:
        """`ok`, or why there is no value: the source's quality, `pending` before its first reading.

        A constant is always `ok`; an unbound input `pending`. A namespace's is the worst of
        what publishes under it, in the order of [rank][flyball.foundation.device.binding.rank].
        """
        if self._constant is not None:
            return Quality.OK
        source = self._follows
        if source is None:
            return Quality.PENDING
        if isinstance(source, Signal):
            reading = source.router.reading(source)
            return Quality.PENDING if reading is None else reading.quality
        readings = [source.device.router.reading(s) for s in source.walk() if Access.P in s.access]
        return min(
            (_quality(r) for r in readings), key=lambda q: rank(*q), default=(Quality.OK, "")
        )[0]

    @property
    def reason(self) -> str:
        """The source's reason for having no value; `""` with one, or while `pending`."""
        reading = self.reading
        return "" if reading is None else reading.reason

    @property
    def at_limit(self) -> Limit | None:
        """The caveat `at_limit` on the source's newest reading, if it has one."""
        reading = self.reading
        return None if reading is None or not reading.usable else reading.at_limit

    def age_s(self, now_ns: int | None = None) -> float | None:
        """Seconds since the rig received the newest reading with a value, on the rig's clock.

        None for a constant, a namespace, and before a first reading with a value. `now_ns`
        defaults to the source's router's now: the rig's clock once the rig has it.
        """
        signal = self.signal
        if signal is None:
            return None
        router = signal.router
        reading = router.reading(signal)
        if reading is not None and not reading.usable:
            reading = router.last_usable.get(signal)
        if reading is None:
            return None
        now = router.now_ns() if now_ns is None else now_ns
        return max(0.0, (now - (reading.received_ns or reading.time_ns)) / 1e9)

    def state(self, now_ns: int | None = None) -> InputState:
        """Everything about it now, in one call: value, quality, reason, caveat, age, followed."""
        try:
            value: Value | None = self.value
        except (NotReadyError, TypeError):
            value = None
        unit = self.unit
        return InputState(
            value=value,
            quality=self.quality,
            reason=self.reason,
            at_limit=self.at_limit,
            age_s=self.age_s(now_ns),
            follows=self.address,
            constant=self._constant,
            unit=None if unit is None else unit.symbol,
        )

    # endregion

    # region Watchers

    def watch(self, callback: Callable[[InputBinding], None]) -> Callable[[], None]:
        """Call `callback(self)` on each new reading on the source; returns the detach.

        For a holder that is not a device (a device is told through
        `Device.inputs_changed`): a program's `settle` step attaches at its
        start and detaches at its end; a controller pulls instead. Called on
        the delivery thread under the rig's lock: read the binding, do not
        block. A callback that raises is logged and left attached.
        """
        self._watchers.append(callback)

        def unwatch() -> None:
            if callback in self._watchers:
                self._watchers.remove(callback)

        return unwatch

    def notify(self) -> None:
        """Call every watcher: the rig's, once per delivery that brought the source a reading."""
        for callback in list(self._watchers):
            try:
                callback(self)
            except Exception:
                log.exception("%s: a watcher raised", self.where)

    # endregion


def _quality(reading: Reading | None) -> tuple[Quality, str]:
    return (Quality.PENDING, "") if reading is None else (reading.quality, reading.reason)


def rank(quality: Quality, reason: str = "") -> int:
    """How much a quality matters, lowest first (A1): what an output of several inputs carries.

    `stale` from its device (`device_offline`, `device_hung`), then `pending`,
    then any other `stale`, then `invalid` and `not_applicable`, then `ok`.
    """
    if quality is Quality.STALE:
        return 0 if reason in (Reason.DEVICE_OFFLINE, Reason.DEVICE_HUNG) else 2
    if quality is Quality.PENDING:
        return 1
    if quality is Quality.OK:
        return 4
    return 3


def values_of(*bindings: InputBinding) -> tuple[Value, ...]:
    """Each binding's value, in order; the error of the one that matters most if any has none.

    What a driver computing one output from several inputs reads them with:
    the output then carries the quality that ranks first
    ([rank][flyball.foundation.device.binding.rank]) -- `stale(device_offline)`
    over `pending` over `invalid`.

    Raises:
        NotReadyError: The first-ranked input is `pending` (or unbound).
        NoValueError: The first-ranked input's source has no value; its `no_value`
            is what to push on the output.
    """
    values: list[Value] = []
    worst: tuple[int, Exception] | None = None
    for binding in bindings:
        try:
            values.append(binding.value)
        except NotReadyError as error:
            no_value = getattr(error, "no_value", None)
            quality, reason = (
                (Quality.PENDING, "") if no_value is None else (no_value.quality, no_value.reason)
            )
            ranked = rank(quality, reason)
            if worst is None or ranked < worst[0]:
                worst = (ranked, error)
    if worst is not None:
        raise worst[1]
    return tuple(values)


__all__ = ["Followable", "InputBinding", "InputState", "rank", "values_of"]
