from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from threading import RLock

from flyball.core import Clock, Reading, require

from ..core.reading import Reading as LegacyReading  # legacy: goes in step 3
from ..core.sink import Actuator
from ..core.units import Unit
from .errors import (
    ControlLawNotSetError,
    ControllerNotStartedError,
    LastReadingNotAvailableError,
)
from .feedforward import Feedforward, FeedforwardConfig, Setpoint
from .setpoint import SetPointGenerator
from .types import (
    ApplyResult,
    ControlLaw,
    ControlLawConfig,
    ControlLawLike,
    ControlLawState,
    ControlLawView,
    RegulateResult,
    Transfer,
    Tuning,
    ValueSource,
)

type LoopReading = Reading | LegacyReading
"""What a loop is ticked with: a reading on its source signal (or, until step 3, its channel)."""
type LoopTickCallback = Callable[[Loop, LoopReading | None], None]


class LoopMode(Enum):
    MANUAL = "manual"
    OPEN = "open"
    REGULATING = "regulating"

    def active(self) -> bool:
        return self is not LoopMode.MANUAL


@dataclass(frozen=True, kw_only=True)
class LoopSettings:
    """What can be re-set while the loop runs: the law in force and its gains."""

    name: str
    law: ControlLawConfig | None
    feedforward: FeedforwardConfig
    """What maps the setpoint (channel unit) to a demand (actuator unit); the law adds to it."""
    demand_unit: str | None
    """The actuator's unit, or None when it takes the channel's."""
    offset_ns: int
    min_period_s: float | None = None
    """Step the law at most this often, however fast readings arrive. None: every reading."""


@dataclass(frozen=True, kw_only=True)
class LoopState:
    law: ControlLawState | None
    correction: float = 0.0
    reference: float | SetPointGenerator | None = None
    setpoint: float | None = None
    """The reference resolved at the last tick: a ramp's value then, in the channel's unit."""
    demand: float | None = None
    expected: float | None = None
    delivered_correction: float | None = None
    mode: LoopMode = LoopMode.MANUAL
    reading: LoopReading | None = None


@dataclass(frozen=True, kw_only=True)
class LoopView(LoopSettings, LoopState):
    law: ControlLawView | None

    @classmethod
    def of(cls, settings: LoopSettings, state: LoopState) -> LoopView:
        return cls(
            name=settings.name,
            law=settings.law and state.law and ControlLawView.of(settings.law, state.law),
            feedforward=settings.feedforward,
            demand_unit=settings.demand_unit,
            offset_ns=settings.offset_ns,
            min_period_s=settings.min_period_s,
            correction=state.correction,
            reference=state.reference,
            setpoint=state.setpoint,
            demand=state.demand,
            expected=state.expected,
            delivered_correction=state.delivered_correction,
            mode=state.mode,
            reading=state.reading,
        )


class Loop[A: Actuator]:
    """Regulates one reading through a law and a feedforward, writing demands somewhere.

    Named by what it drives. Built on an `actuator` (legacy: its name, unit
    and `set_demand`), or on a `name`, a `demand_unit` and a `write` --
    what [Controller][flyball.control.controller.Controller] does.
    """

    clock: Clock
    actuator: A
    """The device driven; unset on a loop built without one."""
    law: ControlLaw | None
    feedforward: Feedforward
    correction: float = 0.0
    offset_ns: int = 0
    reference: float | SetPointGenerator | None = None
    setpoint: float | None = None
    demand: float | None = None
    expected: float | None = None
    reading: LoopReading | None = None
    delivered_correction: float | None = None
    mode: LoopMode = LoopMode.MANUAL
    _on_tick: dict[LoopTickCallback, None]
    lock: RLock
    units: str | None = None
    _base: float | None = None
    """The feedforward's part of the last demand, so a deferred delivery can split the rest."""

    def __init__(
        self,
        clock: Clock,
        actuator: A | None = None,
        law: ControlLaw | ControlLawConfig | ControlLawView | Tuning | None = None,
        min_period_s: float | None = None,
        write: Callable[[float], float | None] | None = None,
        feedforward: Feedforward | FeedforwardConfig | None = None,
        *,
        name: str | None = None,
        demand_unit: Unit | None = None,
    ) -> None:
        self.clock = clock
        if actuator is not None:
            self.actuator = actuator
            name = actuator.name if name is None else name
            demand_unit = actuator.demand_unit if demand_unit is None else demand_unit
            write = actuator.set_demand if write is None else write
        if name is None or write is None:
            raise TypeError("a loop needs an actuator, or a name and a write")
        self._name = name
        self._demand_unit = demand_unit
        if isinstance(feedforward, FeedforwardConfig):
            feedforward = feedforward.build()
        self.feedforward = Setpoint() if feedforward is None else feedforward
        self.write: Callable[[float], float | None] = write
        """How a demand reaches the hardware: the actuator's `set_demand`, a queue in front
        of a bus, or the rig's `demand`."""
        self.law = None
        if law is not None:
            self._set_law(law)
        self.lock = RLock()
        self._on_tick = {}
        self.min_period_s: float | None = min_period_s
        self._last_step_ns: int | None = None

    @property
    def last_value(self) -> float | None:
        return self.reading and self.reading.value

    @property
    def required_last_value(self) -> float:
        return require(self.last_value, LastReadingNotAvailableError)

    @property
    def required_law(self) -> ControlLaw:
        return require(self.law, ControlLawNotSetError)

    @property
    def name(self) -> str:
        """A loop is known by what it drives."""
        return self._name

    @property
    def demand_unit(self) -> Unit | None:
        """What `write` takes; None when it takes the reading's unit."""
        return self._demand_unit

    @property
    def settings(self) -> LoopSettings:
        return LoopSettings(
            name=self.name,
            law=self.law and self.law.config,
            feedforward=self.feedforward.config,
            demand_unit=None if (u := self._demand_unit) is None else u.symbol,
            offset_ns=self.offset_ns,
            min_period_s=self.min_period_s,
        )

    @property
    def state(self) -> LoopState:
        return LoopState(
            law=self.law and self.law.state,
            correction=self.correction,
            reference=self.reference,
            setpoint=self.setpoint,
            demand=self.demand,
            expected=self.expected,
            delivered_correction=self.delivered_correction,
            mode=self.mode,
            reading=self.reading,
        )

    @property
    def view(self) -> LoopView:
        return LoopView.of(settings=self.settings, state=self.state)

    def setpoint_at(self, time_ns: int) -> float:
        if isinstance(self.reference, SetPointGenerator):
            return self.reference.generate(self.clock.from_start_s(time_ns))
        return require(self.reference, ControllerNotStartedError)

    def rate_at(self, time_ns: int) -> float:
        """How fast the setpoint is moving at `time_ns`; 0 off a ramp.

        From the generator, not a difference of successive `setpoint_at`
        values: those carry reading noise a real trajectory does not have.
        """
        if isinstance(self.reference, SetPointGenerator):
            return self.reference.rate(self.clock.from_start_s(time_ns))
        return 0.0

    def demand_at(self, time_ns: int) -> float:
        return self.feedforward(self.setpoint_at(time_ns), self.rate_at(time_ns)) + self.correction

    def _set_law(self, law: ControlLaw | ControlLawConfig | ControlLawView | Tuning) -> None:
        self.law = law if isinstance(law, ControlLaw) else law.build()

    def set_law(self, law: ControlLaw | ControlLawConfig | ControlLawView | Tuning) -> None:
        self._set_law(law)

    def resolve_value(self, at: ValueSource | float, time_ns: int | None = None) -> float:
        if at is ValueSource.PROCESS:
            return self.required_last_value
        if at is ValueSource.SETPOINT:
            return self.setpoint_at(self.get_time_ns(time_ns))
        if at is ValueSource.DEMAND:
            # `demand_at` is in the actuator's unit; every other source here
            # is in the channel's, since the result becomes `self.reference`
            # and is compared against readings by the law. Convert back
            # through the feedforward's inverse rather than handing the raw
            # actuator-unit number back as if it were a setpoint.
            time_ns = self.get_time_ns(time_ns)
            return self.feedforward.invert(self.demand_at(time_ns), self.rate_at(time_ns))
        return float(at)

    def reset_law(self, time_ns: int | None = None) -> None:
        time_ns = self.get_time_ns(time_ns)
        self.offset_ns = time_ns

        if self.law is not None:
            self.law.reset()

    def regulate(
        self,
        at: ValueSource | float,
        generator: SetPointGenerator | None = None,
        tuning: ControlLawLike | None = None,
        time_ns: int | None = None,
        transfer: Transfer = Transfer.TRACK,
    ) -> RegulateResult:
        """Aim at `at` and hand control back to the law.

        The aim is set before the law is seeded, so the seed reproduces the
        delivered output against the new setpoint.

        Args:
            at: Where to aim, or the source to take it from; `SETPOINT` and
                `DEMAND` mean the current ones.
            generator: A trajectory to follow from `at`.
            tuning: A law to swap in first, for a bumpless retune.
            time_ns: The handover instant and the law's new clock origin.
                Defaults to now.
            transfer: How to seed the correction. Degrades rather than fails
                when the mode needs something missing; the bump reports it.

        Returns:
            What was applied, and the step the handover put through the
            actuator; zero when the seed held the output.
        """
        with self.lock:
            time_ns = self.get_time_ns(time_ns)
            held = self.expected if self.expected is not None else self.demand
            setpoint = self.resolve_value(at, time_ns)

            if tuning is not None:
                self._set_law(tuning)

            self.reference = setpoint
            if generator is not None:
                generator.start(self.clock.from_start_s(time_ns), setpoint)
                self.reference = generator

            if transfer is not Transfer.NONE:
                self.reset_law(time_ns)
                setpoint = self.setpoint_at(time_ns)
                reading = self.last_value
                if reading is None or transfer is Transfer.RESET:
                    self.correction = 0.0
                else:
                    hold = (
                        self.correction
                        if transfer is Transfer.CARRY or held is None
                        else held - self.feedforward(setpoint, self.rate_at(time_ns))
                    )
                    if self.law is not None:
                        self.correction = self.law.resume(reading, setpoint, hold)
            self.mode = LoopMode.REGULATING
            applied = self._apply_demand(setpoint, self.rate_at(time_ns))
            bump = 0.0 if held is None else applied.demand - held
            return RegulateResult(*applied, bump=bump)

    def manual(self) -> None:
        """Stop regulating: the actuator keeps its last demand and takes commands directly."""
        with self.lock:
            self.mode = LoopMode.MANUAL

    def set_reference(
        self,
        at: ValueSource | float,
        generator: SetPointGenerator | None = None,
        time_ns: int | None = None,
    ) -> None:
        with self.lock:
            setpoint = self.resolve_value(at)
            self.reference = setpoint
            if generator is not None:
                generator.start(self.clock.from_start_s(self.get_time_ns(time_ns)), setpoint)
                self.reference = generator

    def get_time_ns(self, time_ns: int | None = None) -> int:
        return self.clock.now_ns() if time_ns is None else time_ns

    def to_law_time(self, time_ns: int) -> float:
        return (time_ns - self.offset_ns) / 1e9

    def attach_on_tick(self, callback: LoopTickCallback | None) -> None:
        with self.lock:
            if callback is not None:
                self._on_tick[callback] = None

    def detach_on_tick(self, callback: LoopTickCallback | None) -> None:
        with self.lock:
            if callback is not None:
                self._on_tick.pop(callback)

    def _run_on_tick(self, reading: LoopReading | None) -> None:
        for callback in self._on_tick:
            callback(self, reading)

    def tick(self, reading: LoopReading | None) -> None:
        time_ns = self.get_time_ns(reading and reading.time_ns)
        if reading is not None:
            self.reading = reading
        self._run_on_tick(reading)

        # A fast source updates the reading every time but steps the law at
        # most every ``min_period_s``: the latest value is always there, the
        # controller integrates at its own rate.
        if (
            reading is not None
            and self.min_period_s is not None
            and self._last_step_ns is not None
            and time_ns - self._last_step_ns < self.min_period_s * 1e9
        ):
            return

        if self.mode.active():
            setpoint = self.setpoint_at(time_ns)
            if reading is not None and self.mode is LoopMode.REGULATING:
                self._last_step_ns = time_ns
                self.correction = self.required_law.step(
                    self.to_law_time(time_ns), reading.value, setpoint, self.delivered_correction
                )
            self._apply_demand(setpoint, self.rate_at(time_ns))

    def _apply_demand(self, setpoint: float, rate: float = 0.0) -> ApplyResult:
        self.setpoint = setpoint
        self._base = base = self.feedforward(setpoint, rate)
        self.demand = base + self.correction
        self.expected = self.write(self.demand)
        self.delivered_correction = None if self.expected is None else self.expected - base

        return ApplyResult(
            expected=self.expected,
            demand=self.demand,
            delivered_correction=self.delivered_correction,
        )

    def apply(self, time_ns: int | None = None) -> ApplyResult:
        time_ns = self.get_time_ns(time_ns)
        return self._apply_demand(self.setpoint_at(time_ns), self.rate_at(time_ns))
