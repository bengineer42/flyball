from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from threading import RLock

from flyball.core import Clock, Reading, require

from ..core.sink import Actuator
from .errors import (
    ControlLawNotSetError,
    ControllerNotStartedError,
    LastReadingNotAvailableError,
)
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

type LoopTickCallback = Callable[[Loop, Reading | None], None]


class LoopMode(Enum):
    MANUAL = "manual"
    OPEN = "open"
    REGULATING = "regulating"

    def active(self) -> bool:
        return self is not LoopMode.MANUAL


@dataclass(frozen=True, kw_only=True)
class LoopSpec:
    name: str
    law: ControlLawConfig | None
    offset_ns: int


@dataclass(frozen=True, kw_only=True)
class LoopState:
    law: ControlLawState | None
    correction: float = 0.0
    reference: float | SetPointGenerator | None = None
    demand: float | None = None
    expected: float | None = None
    delivered_correction: float | None = None
    mode: LoopMode = LoopMode.MANUAL
    reading: Reading | None = None


@dataclass(frozen=True, kw_only=True)
class LoopView(LoopSpec, LoopState):
    law: ControlLawView | None

    @classmethod
    def of(cls, spec: LoopSpec, state: LoopState) -> LoopView:
        return cls(
            name=spec.name,
            law=spec.law and state.law and ControlLawView.of(spec.law, state.law),
            offset_ns=spec.offset_ns,
            correction=state.correction,
            reference=state.reference,
            demand=state.demand,
            expected=state.expected,
            delivered_correction=state.delivered_correction,
            mode=state.mode,
            reading=state.reading,
        )


class Loop[A: Actuator]:
    clock: Clock
    actuator: A
    law: ControlLaw | None
    correction: float = 0.0
    offset_ns: int = 0
    reference: float | SetPointGenerator | None = None
    demand: float | None = None
    expected: float | None = None
    reading: Reading | None = None
    delivered_correction: float | None = None
    mode: LoopMode = LoopMode.MANUAL
    _on_tick: dict[LoopTickCallback, None]
    lock: RLock
    units: str | None = None

    def __init__(
        self,
        clock: Clock,
        actuator: A,
        law: ControlLaw | ControlLawConfig | ControlLawView | Tuning | None = None,
    ) -> None:
        self.clock = clock
        self.actuator = actuator
        if law is not None:
            self._set_law(law)
        self.lock = RLock()
        self._on_tick = {}

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
        return self.actuator.name

    @property
    def spec(self) -> LoopSpec:
        return LoopSpec(
            name=self.name,
            law=self.law and self.law.config,
            offset_ns=self.offset_ns,
        )

    @property
    def state(self) -> LoopState:
        return LoopState(
            law=self.law and self.law.state,
            correction=self.correction,
            reference=self.reference,
            demand=self.demand,
            expected=self.expected,
            delivered_correction=self.delivered_correction,
            mode=self.mode,
            reading=self.reading,
        )

    @property
    def view(self) -> LoopView:
        return LoopView.of(
            spec=self.spec,
            state=self.state,
        )

    def setpoint_at(self, time_ns: int) -> float:
        if isinstance(self.reference, SetPointGenerator):
            return self.reference.generate(self.clock.from_start_s(time_ns))
        return require(self.reference, ControllerNotStartedError)

    def demand_at(self, time_ns: int) -> float:
        return self.setpoint_at(time_ns) + self.correction

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
            return self.demand_at(self.get_time_ns(time_ns))
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
        """Aim at ``at`` and hand control back to the law.

        The aim is set before the law is seeded, so the seed reproduces the
        delivered output against the *new* setpoint. Seeding first would leave
        the correction sized for the old one, stepping the actuator by the
        difference and reporting no bump for it.

        Args:
            at: Where to aim, or the source to take it from. Resolved before
                the aim moves, so ``SETPOINT`` and ``DEMAND`` mean the current
                ones.
            generator: A trajectory to follow from ``at``. Held rather than
                resolved, so the setpoint stays exact between ticks.
            tuning: A law to swap in first, for a bumpless retune.
            time_ns: The handover instant, and the law's new clock origin.
                Defaults to now.
            transfer: How to seed the correction across the handover.
                Degrades rather than fails: ``TRACK`` needs something delivered
                and any seeded mode needs a reading to compute the law's
                proportional term from. Where either is missing it falls back,
                and the bump is what tells the caller it did.

        Returns:
            What was applied, and the step the handover put through the
            actuator -- zero when the seed held the output.
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
                        else held - setpoint
                    )
                    if self.law is not None:
                        self.correction = self.law.resume(reading, setpoint, hold)
            self.mode = LoopMode.REGULATING
            applied = self._apply_demand(setpoint)
            bump = 0.0 if held is None else applied.demand - held
            return RegulateResult(*applied, bump=bump)

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

    def _run_on_tick(self, reading: Reading | None) -> None:
        for callback in self._on_tick:
            callback(self, reading)

    def tick(self, reading: Reading | None) -> None:
        time_ns = self.get_time_ns(reading and reading.time_ns)
        if reading is not None:
            self.reading = reading
        self._run_on_tick(reading)

        if self.mode.active():
            setpoint = self.setpoint_at(time_ns)
            if reading is not None and self.mode is LoopMode.REGULATING:
                self.correction = self.required_law.step(
                    self.to_law_time(time_ns), reading.value, setpoint, self.delivered_correction
                )
            self._apply_demand(setpoint)

    def _apply_demand(self, setpoint: float) -> ApplyResult:
        self.demand = setpoint + self.correction
        self.expected = self.actuator.set_demand(self.demand)
        self.delivered_correction = None if self.expected is None else self.expected - setpoint

        return ApplyResult(
            expected=self.expected,
            demand=self.demand,
            delivered_correction=self.delivered_correction,
        )

    def apply(self, time_ns: int | None = None) -> ApplyResult:
        return self._apply_demand(self.setpoint_at(self.get_time_ns(time_ns)))
