"""The laws that ship.

The PID family (`P`, `PI`, `PID`) with setpoint weighting; `IMC`, a PID whose
gains come from a plant model; `on_off`, a relay with hysteresis; `smith`, a
PI on a dead-time-compensated reading; `scheduled`, a PID whose gains follow
the setpoint; `sliding`, a sliding-mode law on an integral surface. Every
one turns `(elapsed, reading, setpoint)` into a correction and is selected by
its type in a rig file, a request or a tuning.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import deque
from itertools import pairwise
from math import exp
from typing import Any, ClassVar

from flyball.model.law import ControlLaw


class Weighted:
    """Setpoint weighting for the proportional term: two-degree-of-freedom PID.

    `b` scales the setpoint in the proportional term only, `kp·(b·r - y)`;
    the integral still acts on the full error, so the loop settles where it
    should. `b = 1` is the textbook law; `b` under 1 softens the kick a
    setpoint step gives the demand without changing how disturbances are
    rejected.
    """

    b: float = 1.0

    def proportional(self, kp: float, reading: float, setpoint: float) -> float:
        return kp * (self.b * setpoint - reading)


class OpenLoop(ControlLaw, type="open_loop"): ...


class P(ControlLaw):
    kp: float

    def __init__(self, kp: float) -> None:
        self.kp = kp

    def resume(self, reading: float, setpoint: float, correction: float) -> float:
        """A proportional law has no memory, so it cannot hold `correction`."""
        return self.kp * (setpoint - reading)

    def step(
        self, elapsed: float, reading: float, setpoint: float, last_applied: float | None = None
    ) -> float:
        return self.kp * (setpoint - reading)


class IComponent:
    _ki: float = 0.0
    _tt_mul_ki: float = 0.0

    integral: float = 0.0
    last_raw: float | None = None
    last_elapsed: float = 0.0

    _state_fields: ClassVar[dict[str, Any]] = {
        "integral": float,
        "last_raw": float | None,
        "last_elapsed": float,
    }

    @property
    def ki(self) -> float:
        return self._ki

    @property
    def tt(self) -> float:
        return self._tt_mul_ki / self._ki if self._ki else 0.0

    @property
    def integral_value(self) -> float:
        return self.integral * self.ki

    def set_ki_tt(self, ki: float, tt: float = 0.0) -> None:
        self._ki = ki
        self._tt_mul_ki = tt * ki

    def reset_integral(self) -> None:
        """Clear the integrator. `last_raw` stays unset so the next step skips anti-windup."""
        self.integral = 0.0
        self.last_raw = None
        self.last_elapsed = 0.0

    def resume_integral(self, kp_term: float, correction: float) -> float:
        """Seed the integrator so the next output is `correction`; return what was seeded.

        `last_raw` is set to the result so the first anti-windup term measures
        against the real resume point. Without an integral the law resumes at
        `kp_term` regardless.

        Args:
            kp_term: The proportional contribution at that instant.
            correction: The offset the next step should reproduce.
        """
        self.integral = 0.0
        self.last_raw = None
        self.last_elapsed = 0.0
        if not self._ki:
            self.last_raw = kp_term
            return kp_term
        self.last_raw = correction
        self.integral = (correction - kp_term) / self._ki
        return correction

    def step_integral(
        self, error: float, elapsed: float, last_applied: float | None = None
    ) -> float:
        """Advance the integrator to `elapsed`; return the interval since the previous step.

        `elapsed` is from the law's own start, so the law keeps no clock. The
        first step and a repeated instant have zero interval and add nothing;
        so does a step back in time (`elapsed` before the last one seen) --
        it is skipped rather than subtracting from the integral, and the next
        step in the right direction picks up from the same `last_elapsed` as
        if the backward one had not happened.

        Args:
            error: Setpoint minus reading.
            elapsed: Seconds since the law was reset or resumed.
            last_applied: What was delivered last step, for back-calculation
                anti-windup: the integral's output moves toward it by
                `(last_applied - last_raw) * (1 - exp(-dt/tt))`, so it never
                crosses it however long the step. Ignored without `tt`.
        """
        dt = elapsed - self.last_elapsed
        if dt < 0.0:
            return 0.0
        self.last_elapsed = elapsed
        self.integral += error * dt
        if self.tt > 0.0 and last_applied is not None and self.last_raw is not None:
            # Back-calculation relaxes the integral's output toward what was
            # applied with time constant tt, integrated exactly over dt: the
            # gap closes by the fraction 1 - exp(-dt/tt), never more. Forward
            # Euler (dt/tt) overshoots once dt > tt and diverges past 2*tt.
            k = 1.0 - exp(-dt / self.tt)
            self.integral += (last_applied - self.last_raw) * k / self._ki
        return dt

    def update_output(self, output: float) -> None:
        self.last_raw = output


class PI(Weighted, IComponent, ControlLaw):
    """Proportional-integral, with back-calculation anti-windup.

    `tt` omitted or 0 disables anti-windup outright (`IComponent.tt` is 0
    whenever `ki` is, too, so a pure-`P`-with-integral-off law never needs
    it either). Recommended `tt` is about `Ti` (`kp/ki`), or `√(Ti·Td)` when
    a derivative term also acts (`PID`, `Scheduled`).
    """

    _kp: float = 0.0

    def __init__(self, kp: float = 0, ki: float = 0, tt: float = 0, b: float = 1.0) -> None:
        self._kp = kp
        self.b = b
        self.set_ki_tt(ki, tt)

    @property
    def kp(self) -> float:
        return self._kp

    def reset(self) -> None:
        self.reset_integral()

    def resume(self, reading: float, setpoint: float, correction: float) -> float:
        return self.resume_integral(self.proportional(self._kp, reading, setpoint), correction)

    def step(
        self, elapsed: float, reading: float, setpoint: float, last_applied: float | None = None
    ) -> float:
        error = setpoint - reading
        self.step_integral(error, elapsed, last_applied)
        output = self.proportional(self._kp, reading, setpoint) + self.integral_value
        self.update_output(output)
        return output


class PID(Weighted, IComponent, ControlLaw):
    """Derivative on the reading, not the error, so a setpoint step does not kick it.

    `tt` omitted or 0 disables anti-windup, as on `PI`; recommended `tt` is
    `√(Ti·Td)` (`Ti = kp/ki`, `Td = kd/kp`) once both act. `n`, when given,
    filters the derivative through a first-order lag with time constant
    `1/n` seconds before it is scaled by `kd` -- raises `n` for less
    filtering, lower for more; omitted (the default), the derivative is the
    raw rate on the reading, exactly as before this option existed.
    """

    _kp: float = 0.0
    _kd: float = 0.0
    n: float | None = None

    last_reading: float | None = None
    filtered_rate: float = 0.0
    """The derivative filter's own state; unused, and always 0, without `n`."""
    _state_fields: ClassVar[dict[str, Any]] = {
        "last_reading": float | None,
        "filtered_rate": float,
    }

    def __init__(
        self,
        kp: float = 0,
        ki: float = 0,
        kd: float = 0,
        tt: float = 0.0,
        b: float = 1.0,
        n: float | None = None,
    ) -> None:
        if n is not None and n <= 0.0:
            raise ValueError("the derivative filter n must be positive")
        self._kp = kp
        self._kd = kd
        self.b = b
        self.n = n
        self.set_ki_tt(ki, tt)

    @property
    def kp(self) -> float:
        return self._kp

    @property
    def kd(self) -> float:
        return self._kd

    def reset(self) -> None:
        self.reset_integral()

    def resume(self, reading: float, setpoint: float, correction: float) -> float:
        self.last_reading = reading
        return self.resume_integral(self.proportional(self._kp, reading, setpoint), correction)

    def step(
        self, elapsed: float, reading: float, setpoint: float, last_applied: float | None = None
    ) -> float:
        error = setpoint - reading
        dt = self.step_integral(error, elapsed, last_applied)
        output = self.proportional(self._kp, reading, setpoint) + self.integral_value
        if dt > 0.0 and self.last_reading is not None:
            rate = (self.last_reading - reading) / dt
            if self.n:
                self.filtered_rate += (dt / (dt + 1.0 / self.n)) * (rate - self.filtered_rate)
                rate = self.filtered_rate
            output += self._kd * rate
        self.last_reading = reading
        self.update_output(output)
        return output


class IMC(PID):
    """A PID whose gains come from a first-order-plus-dead-time model, by the IMC rule.

    The same arithmetic as `flyball.autotune.rules.imc`, stated here so the
    law can be written from the model directly (`{type: IMC, gain: 1, tau: 60,
    dead_time: 5}`) and retuned by changing the model, not the gains. `lam`
    is the closed-loop time constant asked for: smaller is faster and less
    tolerant of model error; it defaults to `max(tau, 0.8·dead_time)`, about
    as fast as the plant already is. `derivative=False` gives the PI form.

    Args:
        gain: Reading per unit of correction at steady state.
        tau: The plant's time constant, seconds.
        dead_time: Its dead time, seconds.
        lam: Closed-loop time constant; None for the default.
        derivative: Include derivative action.
        b: Setpoint weight on the proportional term.
        n: Derivative filter; see `PID`. None (the default) leaves the
            derivative unfiltered.
    """

    gain: float
    tau: float
    dead_time: float
    lam: float | None
    derivative: bool

    def __init__(
        self,
        gain: float,
        tau: float,
        dead_time: float = 0.0,
        lam: float | None = None,
        derivative: bool = True,
        b: float = 1.0,
        n: float | None = None,
    ) -> None:
        if not gain or tau <= 0.0 or dead_time < 0.0:
            raise ValueError("IMC needs a non-zero gain, a positive tau and a dead time >= 0")
        self.gain, self.tau, self.dead_time, self.lam, self.derivative = (
            gain,
            tau,
            dead_time,
            lam,
            derivative,
        )
        closed = max(tau, 0.8 * dead_time) if lam is None else lam
        if derivative:
            ti = tau + dead_time / 2
            kp = ti / (gain * (closed + dead_time / 2))
            td = tau * dead_time / (2 * tau + dead_time) if dead_time else 0.0
        else:
            ti = tau
            kp = tau / (gain * (closed + dead_time))
            td = 0.0
        super().__init__(kp=kp, ki=kp / ti, kd=kp * td, tt=(ti * td) ** 0.5 if td else ti, b=b, n=n)


class OnOff(ControlLaw, type="on_off"):
    """A relay with hysteresis: `high` below the setpoint, `low` above, held in the deadband.

    For an actuator that only switches -- a heater with a contactor, a
    solenoid valve -- under a feedforward of `none`, so the correction is
    the whole demand. `hysteresis` is the half-width of the deadband in the
    reading's units; wider switches less often and holds less tightly.
    """

    high: float
    low: float
    hysteresis: float
    on: bool = False
    _state_fields: ClassVar[dict[str, Any]] = {"on": bool}

    def __init__(self, high: float, low: float = 0.0, hysteresis: float = 0.0) -> None:
        if hysteresis < 0.0:
            raise ValueError("hysteresis cannot be negative")
        self.high, self.low, self.hysteresis = high, low, hysteresis
        self.on = False

    def reset(self) -> None:
        self.on = False

    def resume(self, reading: float, setpoint: float, correction: float) -> float:
        """Take the state whose output is nearer `correction`; the difference is the bump."""
        self.on = abs(correction - self.high) <= abs(correction - self.low)
        return self.high if self.on else self.low

    def step(
        self, elapsed: float, reading: float, setpoint: float, last_applied: float | None = None
    ) -> float:
        error = setpoint - reading
        if error > self.hysteresis:
            self.on = True
        elif error < -self.hysteresis:
            self.on = False
        return self.high if self.on else self.low


class SmithPredictor(PI, type="smith"):
    """A PI on a reading with the dead time taken out: the Smith predictor.

    The law runs its own first-order model of the plant on the corrections it
    has sent. The reading the PI sees is the real one plus the difference
    between the model's undelayed and delayed outputs -- what the plant will
    have done once the dead time has passed -- so the PI can be tuned for the
    lag alone (`lam` near `tau` rather than `tau + dead_time`). Worth it when
    the dead time is comparable to the time constant; below that a PI tuned
    for the whole plant does as well.

    The law never sees the demand's feedforward base, only its own
    correction, so the model is driven by `feedforward * setpoint +
    correction`: `feedforward` is what the controller's feedforward hands on
    per unit of setpoint -- 1 under the default `setpoint` feedforward (a
    drive in the reading's units), 0 under `none` (a raw drive, the
    correction is the whole demand) -- and `gain` is reading per unit of that
    input, as an identified plant reports it. When `last_applied` is given
    -- what the target actually delivered last tick -- the model is driven
    by that rather than the law's own last output, so a clamp or a deferred
    commit downstream does not leave the model believing more correction
    reached the plant than really did.

    Args:
        kp, ki, tt: The PI, tuned for the delay-free plant. `tt` omitted or
            0 disables anti-windup; see `IComponent.step_integral`.
        gain, tau, dead_time: The model.
        feedforward: Demand per unit of setpoint the feedforward contributes.
        b: Setpoint weight on the proportional term.
    """

    gain: float
    tau: float
    dead_time: float
    feedforward: float
    predicted: float = 0.0
    """The model's undelayed output."""
    predicted_delayed: float = 0.0
    """The model's delayed output: what the reading should be showing now."""
    _state_fields: ClassVar[dict[str, Any]] = {"predicted": float, "predicted_delayed": float}

    def __init__(
        self,
        kp: float,
        ki: float,
        gain: float,
        tau: float,
        dead_time: float,
        tt: float = 0.0,
        feedforward: float = 1.0,
        b: float = 1.0,
    ) -> None:
        if tau <= 0.0 or dead_time < 0.0:
            raise ValueError("the model needs a positive tau and a dead time >= 0")
        super().__init__(kp=kp, ki=ki, tt=tt, b=b)
        self.gain, self.tau, self.dead_time = gain, tau, dead_time
        self.feedforward = feedforward
        self.predicted = 0.0
        self.predicted_delayed = 0.0
        self._pipe: deque[tuple[float, float]] = deque()  # (elapsed, predicted)
        self._last_output = 0.0

    def reset(self) -> None:
        super().reset()
        self.predicted = 0.0
        self.predicted_delayed = 0.0
        self._pipe.clear()
        self._last_output = 0.0

    def resume(self, reading: float, setpoint: float, correction: float) -> float:
        # The model starts at rest on the demand in force: no predicted
        # movement is pending, so the PI sees the plain reading.
        self.predicted = self.predicted_delayed = self.gain * (
            self.feedforward * setpoint + correction
        )
        self._pipe.clear()
        self._last_output = correction
        return super().resume(reading, setpoint, correction)

    def step(
        self, elapsed: float, reading: float, setpoint: float, last_applied: float | None = None
    ) -> float:
        dt = elapsed - self.last_elapsed
        if dt > 0.0:
            # The model's lag, driven over the interval by the demand as the
            # model sees it: the setpoint's share now, plus what the target
            # actually delivered last tick when that is known (a clamp or a
            # deferred commit means it is not always the raw correction the
            # law itself returned) -- else the law's own last output.
            driven = self._last_output if last_applied is None else last_applied
            target = self.gain * (self.feedforward * setpoint + driven)
            self.predicted = target + (self.predicted - target) * exp(-dt / self.tau)
            self._pipe.append((elapsed, self.predicted))
            due = elapsed - self.dead_time
            while len(self._pipe) > 1 and self._pipe[1][0] <= due:
                self._pipe.popleft()
            if self._pipe[0][0] <= due:
                self.predicted_delayed = self._pipe[0][1]
        seen = reading + (self.predicted - self.predicted_delayed)
        self._last_output = super().step(elapsed, seen, setpoint, last_applied)
        return self._last_output


class Scheduled(PID, type="scheduled"):
    """A PID whose gains follow the setpoint: gain scheduling.

    `points` is a table of `[setpoint, kp, ki, kd]` rows; the gains in force
    are interpolated between the two rows the setpoint falls between and
    held flat beyond the ends. When `ki` changes the integrator is rescaled
    so its contribution does not jump: the schedule is bumpless.

    For a plant whose response depends on where it is run -- a heater whose
    losses grow with temperature, a valve that is nonlinear in its travel --
    where one tuning is either sluggish at one end or ringing at the other.

    `tt` omitted or 0 disables anti-windup, as on `PI`/`PID`. `n`, as on
    `PID`, filters the derivative and does not change with the schedule.
    """

    points: list[list[float]]
    _state_fields: ClassVar[dict[str, Any]] = {"kp_now": float, "ki_now": float, "kd_now": float}
    kp_now: float = 0.0
    ki_now: float = 0.0
    kd_now: float = 0.0

    def __init__(
        self,
        points: list[list[float]],
        tt: float = 0.0,
        b: float = 1.0,
        n: float | None = None,
    ) -> None:
        rows = sorted((list(map(float, row)) for row in points), key=lambda row: row[0])
        if len(rows) < 1 or any(len(row) != 4 for row in rows):
            raise ValueError("points are [setpoint, kp, ki, kd] rows, at least one")
        if any(a[0] == c[0] for a, c in pairwise(rows)):
            raise ValueError("two rows share a setpoint")
        self.points = rows
        self._setpoints = [row[0] for row in rows]
        self._tt = tt
        _, kp, ki, kd = rows[0]
        super().__init__(kp=kp, ki=ki, kd=kd, tt=tt, b=b, n=n)
        self.kp_now, self.ki_now, self.kd_now = kp, ki, kd

    def gains_at(self, setpoint: float) -> tuple[float, float, float]:
        rows, keys = self.points, self._setpoints
        if setpoint <= keys[0]:
            return tuple(rows[0][1:])  # type: ignore[return-value]
        if setpoint >= keys[-1]:
            return tuple(rows[-1][1:])  # type: ignore[return-value]
        index = bisect_left(keys, setpoint)
        low, high = rows[index - 1], rows[index]
        weight = (setpoint - low[0]) / (high[0] - low[0])
        return tuple(a + (c - a) * weight for a, c in zip(low[1:], high[1:], strict=True))  # type: ignore[return-value]

    def _schedule(self, setpoint: float) -> None:
        kp, ki, kd = self.gains_at(setpoint)
        if ki != self.ki and self.ki and ki:
            self.integral *= self.ki / ki  # the same contribution under the new ki
        self._kp, self._kd = kp, kd
        self.set_ki_tt(ki, self._tt)
        self.kp_now, self.ki_now, self.kd_now = kp, ki, kd

    def resume(self, reading: float, setpoint: float, correction: float) -> float:
        self._schedule(setpoint)
        return super().resume(reading, setpoint, correction)

    def step(
        self, elapsed: float, reading: float, setpoint: float, last_applied: float | None = None
    ) -> float:
        self._schedule(setpoint)
        return super().step(elapsed, reading, setpoint, last_applied)


class SlidingMode(ControlLaw, type="sliding"):
    """Sliding-mode control on an integral surface, with a boundary layer.

    The surface is `s = e + lam·∫e`; the law pushes towards it with
    `k·sat(s / boundary)`, a full `±k` outside the boundary layer and
    proportional inside. On the surface the error decays as a first-order
    system with time constant `1/lam`, whatever the plant's gain within what
    `k` can supply -- the robustness sliding mode is known for. The price is
    chattering: with no boundary layer the output switches sign every tick,
    which suits a power switch and wears a pump or a valve, so `boundary` is
    required and should be a few times the reading's noise. Inside the layer
    the law is a saturated PI with gains `k/boundary` and `k·lam/boundary`.

    Args:
        k: The most correction the law will apply, either way.
        lam: Surface slope, per second: how fast the error is made to decay.
        boundary: Half-width of the boundary layer, in the reading's units.
    """

    k: float
    lam: float
    boundary: float
    integral: float = 0.0
    last_elapsed: float = 0.0
    _state_fields: ClassVar[dict[str, Any]] = {"integral": float, "last_elapsed": float}

    def __init__(self, k: float, lam: float, boundary: float) -> None:
        if k <= 0.0 or lam <= 0.0 or boundary <= 0.0:
            raise ValueError("k, lam and boundary must be positive")
        self.k, self.lam, self.boundary = k, lam, boundary
        self.integral = 0.0
        self.last_elapsed = 0.0

    def reset(self) -> None:
        self.integral = 0.0
        self.last_elapsed = 0.0

    def resume(self, reading: float, setpoint: float, correction: float) -> float:
        """Seed the integral so the surface reproduces `correction`, within `±k`."""
        wanted = min(max(correction, -self.k), self.k)
        error = setpoint - reading
        self.integral = (wanted * self.boundary / self.k - error) / self.lam
        self.last_elapsed = 0.0
        return wanted

    def step(
        self, elapsed: float, reading: float, setpoint: float, last_applied: float | None = None
    ) -> float:
        error = setpoint - reading
        dt = elapsed - self.last_elapsed
        # A step back in time (elapsed before the last one seen) is skipped,
        # like step_integral above: the integral holds and last_elapsed is
        # left where it was, so the next forward step sees the right dt.
        if dt >= 0.0:
            self.last_elapsed = elapsed
            surface = error + self.lam * self.integral
            # Inside the layer the integral runs; at its edge it holds, so the
            # surface cannot wind up while the output is pinned at ±k.
            if abs(surface) < self.boundary or surface * error < 0:
                self.integral += error * dt
        surface = error + self.lam * self.integral
        return self.k * min(max(surface / self.boundary, -1.0), 1.0)
