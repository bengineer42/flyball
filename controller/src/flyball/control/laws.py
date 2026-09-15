from __future__ import annotations

from typing import Any, ClassVar

from .types import ControlLaw, Tuning


class OpenLoop(ControlLaw, tag="open_loop"): ...


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
        first step and a repeated instant have zero interval and add nothing.

        Args:
            error: Setpoint minus reading.
            elapsed: Seconds since the law was reset or resumed.
            last_applied: What was delivered last step, for back-calculation
                anti-windup. Ignored without `tt`.
        """
        dt = elapsed - self.last_elapsed
        self.last_elapsed = elapsed
        self.integral += error * dt
        if self._tt_mul_ki and last_applied is not None and self.last_raw is not None:
            self.integral += (last_applied - self.last_raw) * dt / self._tt_mul_ki
        return dt

    def update_output(self, output: float) -> None:
        self.last_raw = output


class PI(IComponent, ControlLaw):
    _kp: float = 0.0

    def __init__(self, kp: float = 0, ki: float = 0, tt: float = 0) -> None:
        self._kp = kp
        self.set_ki_tt(ki, tt)

    @property
    def kp(self) -> float:
        return self._kp

    def reset(self) -> None:
        self.reset_integral()

    def resume(self, reading: float, setpoint: float, correction: float) -> float:
        return self.resume_integral(self._kp * (setpoint - reading), correction)

    def step(
        self, elapsed: float, reading: float, setpoint: float, last_applied: float | None = None
    ) -> float:
        error = setpoint - reading
        self.step_integral(error, elapsed, last_applied)
        output = self._kp * error + self.integral_value
        self.update_output(output)
        return output


class PID(IComponent, ControlLaw):
    _kp: float = 0.0
    _kd: float = 0.0

    last_reading: float | None = None
    _state_fields: ClassVar[dict[str, Any]] = {"last_reading": float | None}

    def __init__(self, kp: float = 0, ki: float = 0, kd: float = 0, tt: float = 0.0) -> None:
        self._kp = kp
        self._kd = kd
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
        return self.resume_integral(self._kp * (setpoint - reading), correction)

    def step(
        self, elapsed: float, reading: float, setpoint: float, last_applied: float | None = None
    ) -> float:
        error = setpoint - reading
        dt = self.step_integral(error, elapsed, last_applied)
        output = self._kp * error + self.integral_value
        if dt > 0.0 and self.last_reading is not None:
            output += self._kd * (self.last_reading - reading) / dt
        self.last_reading = reading
        self.update_output(output)
        return output


OpenLoopTuning = Tuning("open_loop", OpenLoop.config())
