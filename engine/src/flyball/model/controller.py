"""A controller: one publishing signal regulated through one writable signal.

A W signal has at most one controller, so the controller is named by that
signal's address (`"heaters.heater1"`). The feedforward maps the source's
unit to the target's; the law adds a correction in the target's unit. The
demand reaches the target through a `write` callable the rig injects: it
calls the rig's `demand()` and returns the committed value, or None when
the commit is deferred to the end of the delivery -- then
[delivered][flyball.model.controller.Controller.delivered] closes the tick
with the write state. Until one is injected, a demand is recorded on the
controller and nothing is written.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import NamedTuple

from flyball.foundation import (
    Clock,
    ConflictError,
    Labelled,
    Reading,
    Signal,
    WriteState,
    require,
)
from flyball.foundation.device import Access
from flyball.model.errors import (
    ControlLawNotSetError,
    ControllerNotStartedError,
    LastReadingNotAvailableError,
)
from flyball.model.feedforward import Feedforward, FeedforwardConfig, NoFeedforward, Setpoint
from flyball.model.generator import SetPointGenerator
from flyball.model.law import (
    ControlLaw,
    ControlLawConfig,
    ControlLawLike,
    ControlLawState,
    ControlLawView,
    Transfer,
)

type ControllerTickCallback = Callable[["Controller", Reading | None], None]


OUTAGE_STEPS = 3
"""A gap in the readings longer than this many usual intervals is an outage, not a slow step."""


class ControllerMode(Enum):
    MANUAL = "manual"
    OPEN = "open"
    REGULATING = "regulating"

    def active(self) -> bool:
        return self is not ControllerMode.MANUAL


class ValueSource(Labelled):
    """Where a ramp begins."""

    PROCESS = "process", "The current reading"
    SETPOINT = "setpoint", "The current target"
    DEMAND = "demand", "The current demand"


class ApplyResult(NamedTuple):
    demand: float
    expected: float | None
    delivered_correction: float | None


class RegulateResult(NamedTuple):
    demand: float
    expected: float | None
    delivered_correction: float | None
    bump: float


@dataclass(frozen=True, kw_only=True)
class ControllerSettings:
    """What can be re-set while the controller runs: the law in force and its gains."""

    name: str
    """The target's address."""
    target: str
    """The address of the W signal driven."""
    source: str
    """The address of the P signal regulated."""
    law: ControlLawConfig | None
    feedforward: FeedforwardConfig
    """What maps the setpoint (source unit) to a demand (target unit); the law adds to it."""
    demand_unit: str
    """The target's unit symbol."""
    offset_ns: int
    min_period_s: float | None = None
    """Step the law at most this often, however fast readings arrive. None: every reading."""


@dataclass(frozen=True, kw_only=True)
class ControllerState:
    law: ControlLawState | None
    correction: float = 0.0
    reference: float | SetPointGenerator | None = None
    setpoint: float | None = None
    """The reference resolved at the last tick: a ramp's value then, in the source's unit."""
    arrived: bool = False
    """Whether the reference has landed: a fixed one always has; a trajectory once it finishes."""
    demand: float | None = None
    expected: float | None = None
    delivered_correction: float | None = None
    mode: ControllerMode = ControllerMode.MANUAL
    reading: Reading | None = None


@dataclass(frozen=True, kw_only=True)
class ControllerView(ControllerSettings, ControllerState):
    law: ControlLawView | None

    @classmethod
    def of(cls, settings: ControllerSettings, state: ControllerState) -> ControllerView:
        return cls(
            name=settings.name,
            target=settings.target,
            source=settings.source,
            law=settings.law and state.law and ControlLawView.of(settings.law, state.law),
            feedforward=settings.feedforward,
            demand_unit=settings.demand_unit,
            offset_ns=settings.offset_ns,
            min_period_s=settings.min_period_s,
            correction=state.correction,
            reference=state.reference,
            setpoint=state.setpoint,
            arrived=state.arrived,
            demand=state.demand,
            expected=state.expected,
            delivered_correction=state.delivered_correction,
            mode=state.mode,
            reading=state.reading,
        )


class Controller:
    """Binds `source` (a P signal) to `target` (a W signal) through a law and a feedforward."""

    clock: Clock
    target: Signal
    source: Signal
    law: ControlLaw | None
    feedforward: Feedforward
    correction: float = 0.0
    offset_ns: int = 0
    reference: float | SetPointGenerator | None = None
    setpoint: float | None = None
    demand: float | None = None
    expected: float | None = None
    reading: Reading | None = None
    delivered_correction: float | None = None
    mode: ControllerMode = ControllerMode.MANUAL
    _on_tick: dict[ControllerTickCallback, None]
    lock: RLock
    _base: float | None = None
    """The feedforward's part of the last demand, so a deferred delivery can split the rest."""

    def __init__(
        self,
        clock: Clock,
        target: Signal,
        source: Signal,
        *,
        law: ControlLawLike | None = None,
        feedforward: Feedforward | FeedforwardConfig | None = None,
        min_period_s: float | None = None,
        write: Callable[[float], float | None] | None = None,
    ) -> None:
        if Access.W not in target.access:
            raise ConflictError(f"{target.address} [{target.access}] is not writable")
        if Access.P not in source.access:
            raise ConflictError(f"{source.address} [{source.access}] is not publishing")
        self.clock = clock
        self.target = target
        self.source = source
        same_unit = source.unit == target.unit
        built = feedforward.build() if isinstance(feedforward, FeedforwardConfig) else feedforward
        if built is None:
            built = Setpoint() if same_unit else NoFeedforward()
        elif isinstance(built, Setpoint) and not same_unit:
            # Handing the target demands in the source's unit when it takes
            # another would run happily and do nonsense.
            raise ConflictError(
                f"controller on {source.address} ({source.unit}) cannot pass its setpoint to"
                f" {target.address!r}, which takes demands in {target.unit}"
            )
        self.feedforward = built
        self.write: Callable[[float], float | None] = self._unwired if write is None else write
        """How a demand reaches the target: the rig's `demand`, returning the committed value."""
        self.law = None
        if law is not None:
            self._set_law(law)
        self.lock = RLock()
        self._on_tick = {}
        self.min_period_s: float | None = min_period_s
        self._last_step_ns: int | None = None
        self._step_interval_ns: int | None = None

    @staticmethod
    def _unwired(demand: float) -> float | None:
        """Nothing to write to yet: the demand is recorded on the controller, not delivered."""
        return None

    @property
    def name(self) -> str:
        """A controller is known by what it drives."""
        return self.target.address

    @property
    def demand_unit(self) -> str:
        """The unit `write` takes: the target's."""
        return self.target.unit.symbol

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
    def settings(self) -> ControllerSettings:
        return ControllerSettings(
            name=self.name,
            target=self.target.address,
            source=self.source.address,
            law=self.law and self.law.config,
            feedforward=self.feedforward.config,
            demand_unit=self.demand_unit,
            offset_ns=self.offset_ns,
            min_period_s=self.min_period_s,
        )

    @property
    def state(self) -> ControllerState:
        return ControllerState(
            law=self.law and self.law.state,
            correction=self.correction,
            reference=self.reference,
            setpoint=self.setpoint,
            arrived=self.arrived,
            demand=self.demand,
            expected=self.expected,
            delivered_correction=self.delivered_correction,
            mode=self.mode,
            reading=self.reading,
        )

    @property
    def arrived(self) -> bool:
        """Whether the reference has landed, now: a number has; a trajectory once it finishes.

        False with no reference at all -- there is nothing to have arrived at.
        """
        if isinstance(self.reference, SetPointGenerator):
            return self.reference.finished(self.clock.from_start_s(self.clock.now_ns()))
        return self.reference is not None

    @property
    def view(self) -> ControllerView:
        return ControllerView.of(settings=self.settings, state=self.state)

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

    def _set_law(self, law: ControlLawLike) -> None:
        self.law = law if isinstance(law, ControlLaw) else law.build()

    def set_law(self, law: ControlLawLike) -> None:
        self._set_law(law)

    def resolve_value(self, at: ValueSource | float, time_ns: int | None = None) -> float:
        if at is ValueSource.PROCESS:
            return self.required_last_value
        if at is ValueSource.SETPOINT:
            return self.setpoint_at(self.get_time_ns(time_ns))
        if at is ValueSource.DEMAND:
            # `demand_at` is in the target's unit; every other source here
            # is in the source's, since the result becomes `self.reference`
            # and is compared against readings by the law. Convert back
            # through the feedforward's inverse rather than handing the raw
            # target-unit number back as if it were a setpoint.
            time_ns = self.get_time_ns(time_ns)
            return self.feedforward.invert(self.demand_at(time_ns), self.rate_at(time_ns))
        return float(at)

    def reset_law(self, time_ns: int | None = None) -> None:
        time_ns = self.get_time_ns(time_ns)
        self.offset_ns = time_ns
        self._last_step_ns = None

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
            target; zero when the seed held the output.
        """
        with self.lock:
            time_ns = self.get_time_ns(time_ns)
            held = self.expected if self.expected is not None else self.demand
            setpoint = _finite_aim(self.resolve_value(at, time_ns), at)

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
            self.mode = ControllerMode.REGULATING
            applied = self._apply_demand(setpoint, self.rate_at(time_ns))
            bump = 0.0 if held is None else applied.demand - held
            return RegulateResult(*applied, bump=bump)

    def manual(self) -> None:
        """Stop regulating: the target keeps its last demand and takes demands directly."""
        with self.lock:
            self.mode = ControllerMode.MANUAL

    def set_reference(
        self,
        at: ValueSource | float,
        generator: SetPointGenerator | None = None,
        time_ns: int | None = None,
    ) -> None:
        with self.lock:
            setpoint = _finite_aim(self.resolve_value(at), at)
            self.reference = setpoint
            if generator is not None:
                generator.start(self.clock.from_start_s(self.get_time_ns(time_ns)), setpoint)
                self.reference = generator

    def get_time_ns(self, time_ns: int | None = None) -> int:
        return self.clock.now_ns() if time_ns is None else time_ns

    def to_law_time(self, time_ns: int) -> float:
        return (time_ns - self.offset_ns) / 1e9

    def attach_on_tick(self, callback: ControllerTickCallback | None) -> None:
        with self.lock:
            if callback is not None:
                self._on_tick[callback] = None

    def detach_on_tick(self, callback: ControllerTickCallback | None) -> None:
        with self.lock:
            if callback is not None:
                self._on_tick.pop(callback)

    def _run_on_tick(self, reading: Reading | None) -> None:
        for callback in self._on_tick:
            callback(self, reading)

    def on_reading(self, reading: Reading) -> None:
        """The source signal's node delivered a sample; step the law on its reading."""
        assert reading.signal is self.source, f"{reading.signal} is not {self.source}"
        self.tick(reading)

    def tick(self, reading: Reading | None) -> None:
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
            if reading is not None and self.mode is ControllerMode.REGULATING:
                self._skip_outage(time_ns)
                self._last_step_ns = time_ns
                self.correction = self.required_law.step(
                    self.to_law_time(time_ns), reading.value, setpoint, self.delivered_correction
                )
            self._apply_demand(setpoint, self.rate_at(time_ns))

    def _skip_outage(self, time_ns: int) -> None:
        """Keep a gap in the readings out of the law's time.

        A source that went quiet (a sensor offline, a stalled poll) comes back
        with one reading after the whole gap; stepped as is, the law would
        integrate the error over all of it at once. Past `OUTAGE_STEPS` usual
        intervals, the law's clock is moved on by the gap less one interval,
        so the first step after an outage counts as one ordinary step.
        """
        last, interval = self._last_step_ns, self._step_interval_ns
        if last is None:
            return
        gap = time_ns - last
        if interval is not None and gap > OUTAGE_STEPS * interval:
            self.offset_ns += gap - interval
        elif gap > 0:
            self._step_interval_ns = gap

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

    def delivered(self, state: WriteState) -> None:
        """The deferred commit reported what the target was set to.

        Records `expected` and `delivered_correction` as `_apply_demand`
        would have, had `write` returned the value at once.
        """
        self.expected = state.value
        self.delivered_correction = (
            None if state.value is None or self._base is None else state.value - self._base
        )


def _finite_aim(setpoint: float, at: ValueSource | float) -> float:
    """`setpoint`, or a refusal before anything changes: a NaN or infinite aim poisons the law."""
    if not math.isfinite(setpoint):
        raise ValueError(f"Setpoint is not finite: {setpoint!r} (from {at!r})")
    return setpoint
