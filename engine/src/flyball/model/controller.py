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

A third, `on_reference`, is called after the reference or the mode changes
(`regulate`, `set_setpoint`, `manual`): the rig arms or cancels
[reapply][flyball.model.controller.Controller.reapply] on its clock, which
follows a moving setpoint's feedforward between readings (E25).
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


class FaultAction(Enum):
    """What a controller does once its source's outage is released (`on_fault`)."""

    FREEZE = "freeze"
    """Nothing more: the law stays frozen, the output where it was; it resumes by itself."""
    MANUAL = "manual"
    """To manual, latched: the output keeps its last value; a person decides."""
    STOP = "stop"
    """To manual, and the target signal's resolved stop written; the signal latched."""
    STOP_DEVICE = "stop_device"
    """To manual, and the target's whole device stopped; the device latched."""

    @property
    def rank(self) -> int:
        """How far it goes: a law error takes the stricter of `manual` and the configured one."""
        return _FAULT_RANKS[self]


_FAULT_RANKS = {
    FaultAction.FREEZE: 0,
    FaultAction.MANUAL: 1,
    FaultAction.STOP: 2,
    FaultAction.STOP_DEVICE: 3,
}


@dataclass(frozen=True)
class OnFault:
    """A controller's `on_fault`: an action, and how long a fault is frozen before it.

    `freeze_s` None: the action waits the rig's default for the fault's reason (A5);
    set: that much fault time accrued, measured across flicker (`{freeze_s: d, then: a}`).
    Plain `freeze` (the default) never acts.
    """

    action: FaultAction = FaultAction.FREEZE
    freeze_s: float | None = None

    def __post_init__(self) -> None:
        if self.freeze_s is not None and not (math.isfinite(self.freeze_s) and self.freeze_s >= 0):
            raise ValueError(f"on_fault freeze_s {self.freeze_s!r}: a finite number of seconds")
        if self.freeze_s is not None and self.action is FaultAction.FREEZE:
            raise ValueError("on_fault: `then` must be manual, stop or stop_device, not freeze")

    @classmethod
    def parse(cls, value: object) -> OnFault:
        """From the rig file's form: an action's name, or `{freeze_s: <s>, then: <action>}`."""
        if isinstance(value, OnFault):
            return value
        if value is None:
            return cls()
        if isinstance(value, str):
            return cls(FaultAction(value))
        if isinstance(value, dict) and set(value) == {"freeze_s", "then"}:
            return cls(FaultAction(value["then"]), float(value["freeze_s"]))
        raise ValueError(
            f"on_fault {value!r}: freeze, manual, stop, stop_device, or {{freeze_s: <s>, then:"
            " <action>}"
        )

    def document(self) -> str | dict[str, object]:
        """The rig file's form: the action's name, or `{freeze_s, then}`."""
        if self.freeze_s is None:
            return self.action.value
        return {"freeze_s": self.freeze_s, "then": self.action.value}

    def escalated(self) -> FaultAction:
        """The action a law error takes: this one, or `manual` if this one is milder."""
        return max(self.action, FaultAction.MANUAL, key=lambda a: a.rank)


class ValueSource(Labelled):
    """Where a ramp begins."""

    MEASURED = "measured", "The current measured value"
    SETPOINT = "setpoint", "The current setpoint"
    OUTPUT = "output", "The current output"


class ApplyResult(NamedTuple):
    output_value: float
    expected: float | None
    delivered_correction: float | None


class RegulateResult(NamedTuple):
    output_value: float
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
    setpoint_period_s: float | None = None
    """Re-apply a moving setpoint's feedforward this often between readings. None: the rig's
    default, `max(0.1 s, poll_s / 4)` from the measured signal's `poll_s`."""
    on_fault: OnFault = OnFault()
    """What it does once its source's outage is released."""


@dataclass(frozen=True, kw_only=True)
class ControllerState:
    law: ControlLawState | None
    correction: float = 0.0
    reference: float | SetpointGenerator | None = None
    setpoint: float | None = None
    """The reference resolved at the last tick: a ramp's value then, in the measured unit."""
    arrived: bool = False
    """Whether the reference has landed: a fixed one always has; a trajectory once it finishes."""
    output_value: float | None = None
    """The last output value asked of the output signal, in its unit."""
    expected: float | None = None
    delivered_correction: float | None = None
    mode: ControllerMode = ControllerMode.MANUAL
    measured_value: Reading | None = None
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
            setpoint_period_s=spec.setpoint_period_s,
            on_fault=spec.on_fault,
            correction=state.correction,
            reference=state.reference,
            setpoint=state.setpoint,
            arrived=state.arrived,
            output_value=state.output_value,
            expected=state.expected,
            delivered_correction=state.delivered_correction,
            mode=state.mode,
            measured_value=state.measured_value,
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
    output_value: float | None = None
    expected: float | None = None
    measured_value: Reading | None = None
    delivered_correction: float | None = None
    mode: ControllerMode = ControllerMode.MANUAL
    _on_tick: dict[ControllerTickCallback, None]
    lock: RLock
    _base: float | None = None
    """The feedforward's part of the last output, so a deferred delivery can split the rest."""
    held: Code | None = None
    """Why the last tick was held (`stale_input`, `limit_unknown`); None when it stepped."""
    declared_label: str | None = None
    """The display name the rig file's entry gave (`label`), or None; `label` resolves it."""

    def __init__(
        self,
        clock: Clock,
        output_signal: Signal,
        measured_signal: Signal,
        *,
        law: ControlLawLike | None = None,
        feedforward: Feedforward | FeedforwardConfig | None = None,
        min_period_s: float | None = None,
        setpoint_period_s: float | None = None,
        write: Callable[[float], float | None] | None = None,
        hold: Callable[[], Code | None] | None = None,
        on_fault: OnFault | None = None,
        label: str | None = None,
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
        self.declared_label = label or None
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
        self.on_reference: Callable[[], None] = self._nothing
        """Called after the reference or the mode changes: the rig arms `reapply`."""
        self.guard: Callable[[], str | None] = self._never_refused
        """Why `regulate` is refused now (a latch held on it or its output), or None."""
        self.on_reseed: Callable[[float | None, float | None], None] = self._reseeded
        """Called with a trajectory's old and new end times when a resume re-seeds it."""
        self.on_fault: OnFault = OnFault() if on_fault is None else on_fault
        self.setpoint_period_s: float | None = setpoint_period_s
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

    @staticmethod
    def _nothing() -> None:
        """No rig to tell of a new reference."""

    @staticmethod
    def _never_refused() -> str | None:
        """No rig to hold a latch: `regulate` is never refused."""
        return None

    @staticmethod
    def _reseeded(was: float | None, now: float | None) -> None:
        """No rig to tell of a re-seeded trajectory."""

    @property
    def name(self) -> str:
        """A controller is known by what it drives: its output's address."""
        return self.output_signal.address

    @property
    def label(self) -> str:
        """What a person reads: the declared label, else its output signal's (D-086)."""
        return self.declared_label or self.output_signal.label

    @property
    def output_unit(self) -> str:
        """The unit `write` takes: the output's."""
        return self.output_signal.unit.symbol

    @property
    def last_value(self) -> float | None:
        """The last measured value; None before one, or while the newest reading has none."""
        measured = self.measured_value
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
            setpoint_period_s=self.setpoint_period_s,
            on_fault=self.on_fault,
        )

    @property
    def state(self) -> ControllerState:
        return ControllerState(
            law=self.law and self.law.state,
            correction=self.correction,
            reference=self.reference,
            setpoint=self.setpoint,
            arrived=self.arrived,
            output_value=self.output_value,
            expected=self.expected,
            delivered_correction=self.delivered_correction,
            mode=self.mode,
            measured_value=self.measured_value,
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

        Raises:
            ConflictError: A latch is held on the controller or its output (a
                stop, an `on_fault` action): only a person's Reset clears it,
                and this changes nothing until then.
        """
        with self.lock:
            if (refused := self.guard()) is not None:
                raise ConflictError(refused)
            time_ns = self.get_time_ns(time_ns)
            held = self.expected if self.expected is not None else self.output_value
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
            bump = 0.0 if held is None else applied.output_value - held
        self.on_reference()
        return RegulateResult(*applied, bump=bump)

    def manual(self) -> None:
        """Stop regulating: the output keeps its last value and takes demands directly."""
        with self.lock:
            self.mode = ControllerMode.MANUAL
        self.on_reference()

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
        self.on_reference()

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
            self.measured_value = reading
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
            if resumed and reading is not None:
                self._reseed(time_ns, reading.value)
            setpoint = self.setpoint_at(time_ns)
            if reading is not None:
                self._skip_outage(time_ns, resumed=resumed)
                self._last_step_ns = time_ns
                self.correction = self.required_law.update(
                    self.to_law_time(time_ns), reading.value, setpoint, self.delivered_correction
                )
            self._apply_output(setpoint, self.rate_at(time_ns))

    def _reseed(self, time_ns: int, value: float) -> None:
        """Resuming after a hold on a trajectory: start what is left of it from `value`.

        The trajectory's clock ran on through the hold, so the setpoint moved away while
        nothing was written; stepped at once, the law would chase the whole jump. Instead
        the segment in force walks on from the reading at its own rate (a ramp's), never
        faster, and so ends later if it has further to go (answer 4).
        """
        reference = self.reference
        if not isinstance(reference, SetpointGenerator):
            return
        now_s = self.clock.from_start_s(time_ns)
        if reference.finished(now_s):
            return
        was = reference.end_time
        if reference.reseed(now_s, float(value)):
            self.on_reseed(was, reference.end_time)

    def follows(self, time_ns: int) -> bool:
        """Whether a moving setpoint is being followed now (E25).

        Regulating, on a generator that has not finished, through a feedforward: what
        `reapply` needs to do anything.
        """
        reference = self.reference
        return (
            self.mode.active()
            and isinstance(reference, SetpointGenerator)
            and not isinstance(self.feedforward, NoFeedforward)
            and not reference.finished(self.clock.from_start_s(time_ns))
        )

    def reapply(self, time_ns: int) -> bool:
        """Between readings: the feedforward of the setpoint now, plus the last correction (E25).

        The law is not stepped: there is no measurement to step it on. Nothing
        happens -- False -- in MANUAL, off a moving setpoint, while held (it
        tests `held`, never sets or clears it), while the rig would hold a
        write (`hold`), when the last measured reading has no value, or when
        the output would not change. `_last_step_ns`, the step interval and
        the law's clock are left alone, so the next reading steps as it would
        have. Returns whether it wrote.
        """
        with self.lock:
            if not self.follows(time_ns) or self.held is not None:
                return False
            if self.measured_value is not None and not self.measured_value.usable:
                return False
            if self.hold() is not None:
                return False
            setpoint = self.setpoint_at(time_ns)
            rate = self.rate_at(time_ns)
            if self.feedforward(setpoint, rate) + self.correction == self.output_value:
                return False
            self._apply_output(setpoint, rate)
            return True

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
        self.output_value = base + self.correction
        self.expected = self.write(self.output_value)
        self.delivered_correction = None if self.expected is None else self.expected - base

        return ApplyResult(
            expected=self.expected,
            output_value=self.output_value,
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
