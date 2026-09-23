"""A controller: one measured signal regulated through one output, a demand.

A demand has at most one controller, so the controller is named by its
output's address (`"heaters.heater1"`). The feedforward maps the measured
signal's unit to the output's; the law adds a correction in the output's
unit. The output value reaches the output signal through a `write` callable
the rig injects: it calls the rig's `demand()` and returns the committed value, or None when
the commit is deferred to the end of the delivery -- then
[delivered][flyball.model.controller.Controller.delivered] closes the tick
with the write state. Until one is injected, the output value is recorded on
the controller and nothing is written.

A second injected callable, `hold`, says whether the rig would refuse the
write now (a stale measured signal, a limit not yet known, a measured signal with no
value: `frozen`) and why. While it does,
the controller is frozen: the law does not step and nothing is written, so
the integral cannot wind up against a write that never lands; the first
step after the hold counts as one ordinary interval.
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
from flyball.foundation.device import Access, Code, Role
from flyball.model.errors import (
    ControlLawNotSetError,
    ControllerNotStartedError,
    LastReadingNotAvailableError,
)
from flyball.model.feedforward import Feedforward, FeedforwardConfig, Identity, NoFeedforward
from flyball.model.generator import SetpointGenerator
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
    """Who drives the output: a person (`manual`) or the law (`regulating`).

    A controller running the `open_loop` law is `regulating`: open loop is a
    law, not a mode.
    """

    MANUAL = "manual"
    REGULATING = "regulating"

    def active(self) -> bool:
        return self is ControllerMode.REGULATING


class ValueSource(Labelled):
    """Where a ramp begins."""

    MEASURED = "measured", "The current measured value"
    SETPOINT = "setpoint", "The current setpoint"
    OUTPUT = "output", "The current output"


class ApplyResult(NamedTuple):
    output: float
    expected: float | None
    delivered_correction: float | None


class RegulateResult(NamedTuple):
    output: float
    expected: float | None
    delivered_correction: float | None
    bump: float


@dataclass(frozen=True, kw_only=True)
class ControllerSpec:
    """What a controller is: its signals, and the law, tuning and feedforward in force.

    A retune or a `regulate` may change the law and its tuning while it runs.
    """

    name: str
    """The output's address."""
    output_signal: str
    """The address of the demand driven: the output."""
    measured_signal: str
    """The address of the P signal regulated: the measured signal."""
    law: ControlLawConfig | None
    feedforward: FeedforwardConfig
    """Maps the setpoint (measured unit) to an output value (output unit); the law adds to it."""
    output_unit: str
    """The output's unit symbol."""
    offset_ns: int
    min_period_s: float | None = None
    """Update the law at most this often, however fast readings arrive. None: every reading."""


@dataclass(frozen=True, kw_only=True)
class ControllerState:
    law: ControlLawState | None
    correction: float = 0.0
    reference: float | SetpointGenerator | None = None
    setpoint: float | None = None
    """The reference resolved at the last tick: a ramp's value then, in the measured unit."""
    arrived: bool = False
    """Whether the reference has landed: a fixed one always has; a trajectory once it finishes."""
    output: float | None = None
    """The last output value asked of the output signal, in its unit."""
    expected: float | None = None
    delivered_correction: float | None = None
    mode: ControllerMode = ControllerMode.MANUAL
    measured: Reading | None = None
    """The last reading of the measured signal."""


@dataclass(frozen=True, kw_only=True)
class ControllerView(ControllerSpec, ControllerState):
    law: ControlLawView | None

    @classmethod
    def of(cls, spec: ControllerSpec, state: ControllerState) -> ControllerView:
        return cls(
            name=spec.name,
            output_signal=spec.output_signal,
            measured_signal=spec.measured_signal,
            law=spec.law and state.law and ControlLawView.of(spec.law, state.law),
            feedforward=spec.feedforward,
            output_unit=spec.output_unit,
            offset_ns=spec.offset_ns,
            min_period_s=spec.min_period_s,
            correction=state.correction,
            reference=state.reference,
            setpoint=state.setpoint,
            arrived=state.arrived,
            output=state.output,
            expected=state.expected,
            delivered_correction=state.delivered_correction,
            mode=state.mode,
            measured=state.measured,
        )


class Controller:
    """Regulates `measured_signal` (P) by writing `output_signal` (a W demand): law, feedforward."""

    clock: Clock
    output_signal: Signal
    measured_signal: Signal
    law: ControlLaw | None
    feedforward: Feedforward
    correction: float = 0.0
    offset_ns: int = 0
    reference: float | SetpointGenerator | None = None
    setpoint: float | None = None
    output: float | None = None
    expected: float | None = None
    measured: Reading | None = None
    delivered_correction: float | None = None
    mode: ControllerMode = ControllerMode.MANUAL
    _on_tick: dict[ControllerTickCallback, None]
    lock: RLock
    _base: float | None = None
    """The feedforward's part of the last output, so a deferred delivery can split the rest."""
    held: Code | None = None
    """Why the last tick was held (`stale_input`, `limit_unknown`); None when it stepped."""

    def __init__(
        self,
        clock: Clock,
        output_signal: Signal,
        measured_signal: Signal,
        *,
        law: ControlLawLike | None = None,
        feedforward: Feedforward | FeedforwardConfig | None = None,
        min_period_s: float | None = None,
        write: Callable[[float], float | None] | None = None,
        hold: Callable[[], Code | None] | None = None,
    ) -> None:
        if output_signal.role is not Role.DEMAND:
            raise ConflictError(
                f"'{output_signal.address}' is a {output_signal.role.value}, not a demand:"
                " a controller drives only demands"
            )
        if Access.W not in output_signal.access:
            raise ConflictError(f"{output_signal.address} [{output_signal.access}] is not writable")
        if Access.P not in measured_signal.access:
            raise ConflictError(
                f"{measured_signal.address} [{measured_signal.access}] is not published"
            )
        self.clock = clock
        self.output_signal = output_signal
        self.measured_signal = measured_signal
        same_unit = measured_signal.unit == output_signal.unit
        built = feedforward.build() if isinstance(feedforward, FeedforwardConfig) else feedforward
        if built is None:
            built = Identity() if same_unit else NoFeedforward()
        elif isinstance(built, Identity) and not same_unit:
            # Handing the output values in the measured unit when it takes
            # another would run happily and do nonsense.
            raise ConflictError(
                f"controller on {measured_signal.address} ({measured_signal.unit}) cannot pass"
                f" its setpoint to {output_signal.address!r}, which takes demands in"
                f" {output_signal.unit}"
            )
        self.feedforward = built
        self.write: Callable[[float], float | None] = self._unwired if write is None else write
        """How an output value reaches the output: the rig's `demand`, returning what committed."""
        self.hold: Callable[[], Code | None] = self._never_held if hold is None else hold
        """Why the rig would refuse a write now, or None: asked before the law steps."""
        self.law = None
        if law is not None:
            self._set_law(law)
        self.lock = RLock()
        self._on_tick = {}
        self.min_period_s: float | None = min_period_s
        self._last_step_ns: int | None = None
        self._step_interval_ns: int | None = None

    @staticmethod
    def _unwired(output: float) -> float | None:
        """Nothing to write to yet: the output is recorded on the controller, not delivered."""
        return None

    @staticmethod
    def _never_held() -> Code | None:
        """Nothing to refuse a write: no rig in front of the output."""
        return None

    @property
    def name(self) -> str:
        """A controller is known by what it drives: its output's address."""
        return self.output_signal.address

    @property
    def output_unit(self) -> str:
        """The unit `write` takes: the output's."""
        return self.output_signal.unit.symbol

    @property
    def last_value(self) -> float | None:
        """The last measured value; None before one, or while the newest reading has none."""
        measured = self.measured
        return None if measured is None or not measured.usable else measured.value

    @property
    def required_last_value(self) -> float:
        return require(self.last_value, LastReadingNotAvailableError)

    @property
    def required_law(self) -> ControlLaw:
        return require(self.law, ControlLawNotSetError)

    @property
    def spec(self) -> ControllerSpec:
        return ControllerSpec(
            name=self.name,
            output_signal=self.output_signal.address,
            measured_signal=self.measured_signal.address,
            law=self.law and self.law.config,
            feedforward=self.feedforward.config,
            output_unit=self.output_unit,
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
            output=self.output,
            expected=self.expected,
            delivered_correction=self.delivered_correction,
            mode=self.mode,
            measured=self.measured,
        )

    @property
    def arrived(self) -> bool:
        """Whether the reference has landed, now: a number has; a trajectory once it finishes.

        False with no reference at all -- there is nothing to have arrived at.
        """
        if isinstance(self.reference, SetpointGenerator):
            return self.reference.finished(self.clock.from_start_s(self.clock.now_ns()))
        return self.reference is not None

    @property
    def view(self) -> ControllerView:
        return ControllerView.of(spec=self.spec, state=self.state)

    def setpoint_at(self, time_ns: int) -> float:
        if isinstance(self.reference, SetpointGenerator):
            return self.reference.generate(self.clock.from_start_s(time_ns))
        return require(self.reference, ControllerNotStartedError)

    def rate_at(self, time_ns: int) -> float:
        """How fast the setpoint is moving at `time_ns`; 0 off a ramp.

        From the generator, not a difference of successive `setpoint_at`
        values: those carry reading noise a real trajectory does not have.
        """
        if isinstance(self.reference, SetpointGenerator):
            return self.reference.rate(self.clock.from_start_s(time_ns))
        return 0.0

    def output_at(self, time_ns: int) -> float:
        return self.feedforward(self.setpoint_at(time_ns), self.rate_at(time_ns)) + self.correction

    def _set_law(self, law: ControlLawLike) -> None:
        self.law = law if isinstance(law, ControlLaw) else law.build()

    def set_law(self, law: ControlLawLike) -> None:
        self._set_law(law)

    def resolve_value(self, at: ValueSource | float, time_ns: int | None = None) -> float:
        if at is ValueSource.MEASURED:
            return self.required_last_value
        if at is ValueSource.SETPOINT:
            return self.setpoint_at(self.get_time_ns(time_ns))
        if at is ValueSource.OUTPUT:
            # `output_at` is in the output's unit; every other value here is
            # in the measured unit, since the result becomes `self.reference`
            # and is compared against readings by the law. Convert back
            # through the feedforward's inverse rather than handing the raw
            # output-unit number back as if it were a setpoint.
            time_ns = self.get_time_ns(time_ns)
            return self.feedforward.invert(self.output_at(time_ns), self.rate_at(time_ns))
        return float(at)

    def clear_law(self, time_ns: int | None = None) -> None:
        time_ns = self.get_time_ns(time_ns)
        self.offset_ns = time_ns
        self._last_step_ns = None

        if self.law is not None:
            self.law.reset()

    def regulate(
        self,
        at: ValueSource | float,
        generator: SetpointGenerator | None = None,
        tuning: ControlLawLike | None = None,
        time_ns: int | None = None,
        transfer: Transfer = Transfer.TRACK,
    ) -> RegulateResult:
        """Aim at `at` and hand control back to the law.

        The aim is set before the law is seeded, so the seed reproduces the
        delivered output against the new setpoint.

        Args:
            at: Where to aim, or the value to take it from; `MEASURED`,
                `SETPOINT` and `OUTPUT` mean the current ones.
            generator: A trajectory to follow from `at`.
            tuning: A law to swap in first, for a bumpless retune.
            time_ns: The handover instant and the law's new clock origin.
                Defaults to now.
            transfer: How to seed the correction. Degrades rather than fails
                when the mode needs something missing; the bump reports it.

        Returns:
            What was applied, and the step the handover put through the
            output; zero when the seed held it.
        """
        with self.lock:
            time_ns = self.get_time_ns(time_ns)
            held = self.expected if self.expected is not None else self.output
            setpoint = _finite_aim(self.resolve_value(at, time_ns), at)

            if tuning is not None:
                self._set_law(tuning)

            self.reference = setpoint
            if generator is not None:
                generator.start(self.clock.from_start_s(time_ns), setpoint)
                self.reference = generator

            if transfer is not Transfer.NONE:
                self.clear_law(time_ns)
                setpoint = self.setpoint_at(time_ns)
                reading = self.last_value
                if reading is None or transfer is Transfer.COLD:
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
            applied = self._apply_output(setpoint, self.rate_at(time_ns))
            bump = 0.0 if held is None else applied.output - held
            return RegulateResult(*applied, bump=bump)

    def manual(self) -> None:
        """Stop regulating: the output keeps its last value and takes demands directly."""
        with self.lock:
            self.mode = ControllerMode.MANUAL

    def set_setpoint(
        self,
        at: ValueSource | float,
        generator: SetpointGenerator | None = None,
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
        """The measured signal's node delivered a sample; update the law on its reading."""
        assert reading.signal is self.measured_signal, (
            f"{reading.signal} is not {self.measured_signal}"
        )
        self.tick(reading)

    def tick(self, reading: Reading | None) -> None:
        time_ns = self.get_time_ns(reading and reading.time_ns)
        if reading is not None:
            self.measured = reading
        self._run_on_tick(reading)

        # A fast measured signal updates the reading every time but steps the law at
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
            # The rig would refuse the write: freeze. Stepping the law against
            # a demand that never lands winds the integral up (back-calculation
            # has no delivered value to pull against) and slams the output
            # when the hold ends. The law's clock skips the hold on return.
            reason = self.hold()
            if reason is None and reading is not None and not reading.usable:
                reason = Code.FROZEN  # no value to step on, rig or not: never substituted
            if reason is not None:
                self.held = reason
                return
            resumed, self.held = self.held is not None, None
            setpoint = self.setpoint_at(time_ns)
            if reading is not None:
                self._skip_outage(time_ns, resumed=resumed)
                self._last_step_ns = time_ns
                self.correction = self.required_law.update(
                    self.to_law_time(time_ns), reading.value, setpoint, self.delivered_correction
                )
            self._apply_output(setpoint, self.rate_at(time_ns))

    def _skip_outage(self, time_ns: int, *, resumed: bool = False) -> None:
        """Keep a gap in the readings out of the law's time.

        A measured signal that went quiet (a sensor offline, a stalled poll) comes back
        with one reading after the whole gap; stepped as is, the law would
        integrate the error over all of it at once. Past `OUTAGE_STEPS` usual
        intervals, the law's clock is moved on by the gap less one interval,
        so the first step after an outage counts as one ordinary step. The
        first step after a hold (`resumed`) is treated the same way whatever
        the gap's length: at most one usual interval, none if there is no
        usual interval yet.
        """
        last, interval = self._last_step_ns, self._step_interval_ns
        if last is None:
            return
        gap = time_ns - last
        if resumed:
            self.offset_ns += max(0, gap - (interval or 0))
        elif interval is not None and gap > OUTAGE_STEPS * interval:
            self.offset_ns += gap - interval
        elif gap > 0:
            self._step_interval_ns = gap

    def _apply_output(self, setpoint: float, rate: float = 0.0) -> ApplyResult:
        self.setpoint = setpoint
        self._base = base = self.feedforward(setpoint, rate)
        self.output = base + self.correction
        self.expected = self.write(self.output)
        self.delivered_correction = None if self.expected is None else self.expected - base

        return ApplyResult(
            expected=self.expected,
            output=self.output,
            delivered_correction=self.delivered_correction,
        )

    def apply(self, time_ns: int | None = None) -> ApplyResult:
        time_ns = self.get_time_ns(time_ns)
        return self._apply_output(self.setpoint_at(time_ns), self.rate_at(time_ns))

    def delivered(self, state: WriteState) -> None:
        """The deferred commit reported what the output was set to.

        Records `expected` and `delivered_correction` as `_apply_output`
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
