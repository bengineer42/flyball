from __future__ import annotations

from typing import Any, ClassVar

from .types import ControlLaw, Tuning


class OpenLoop(ControlLaw, tag="open_loop"): ...


class P(ControlLaw):
    kp: float

    def __init__(self, kp: float) -> None:
        self.kp = kp

    def resume(self, time: float, reading: float, setpoint: float, correction: float) -> float:
        """A proportional law has no memory, so it cannot hold ``correction``."""
        return self.kp * (setpoint - reading)

    def step(
        self, time: float, reading: float, setpoint: float, last_applied: float | None = None
    ) -> float:
        error = setpoint - reading
        output = self.kp * error
        return output


class IComponent:
    _ki: float = 0.0
    _tt_mul_ki: float = 0.0

    integral: float = 0.0
    last_raw: float | None = None
    last_time: float = 0.0

    _state_fields: ClassVar[dict[str, Any]] = {
        "integral": float,
        "last_raw": float | None,
        "last_time": float,
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

    def start_integral(self, time: float) -> None:
        """Reset the integrator.

        ``last_raw`` stays unset so the first step skips the anti-windup term,
        which has no previous output to compare.
        """
        self.last_time = time
        self.integral = 0.0
        self.last_raw = None

    def resume_integral(self, time: float, kp_term: float, correction: float) -> float:
        """Seed the integrator so the next output is ``correction``.

        ``last_raw`` is set to whatever is returned rather than left unset, so the
        first step's anti-windup term measures against the output the law really
        is resuming from.

        Args:
            time: The resume instant.
            kp_term: The law's proportional contribution at that instant, which
                the integral has to cancel.
            correction: The offset the next step should reproduce.

        Returns:
            The correction actually seeded. With no integral there is nothing to
            hold the offset, so the law resumes at ``kp_term`` however much was
            asked for.
        """
        self.start_integral(time)
        if not self._ki:
            self.last_raw = kp_term
            return kp_term
        self.last_raw = correction
        self.integral = (correction - kp_term) / self._ki
        return correction

    def step_integral(self, error: float, time: float, last_applied: float | None = None) -> float:
        dt = time - self.last_time

        if dt <= 0:
            raise ValueError("Time must be increasing")

        self.integral += error * dt
        if self._tt_mul_ki and last_applied is not None and self.last_raw is not None:
            self.integral += (last_applied - self.last_raw) * dt / self._tt_mul_ki

        self.last_time = time

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

    def start(self, time: float) -> None:
        self.start_integral(time)

    def resume(self, time: float, reading: float, setpoint: float, correction: float) -> float:
        return self.resume_integral(time, self._kp * (setpoint - reading), correction)

    def step(
        self, time: float, reading: float, setpoint: float, last_applied: float | None = None
    ) -> float:
        error = setpoint - reading
        self.step_integral(error, time, last_applied)
        output = self._kp * error + self.integral_value
        self.update_output(output)
        return output


class PID(ControlLaw, IComponent):
    _kp: float = 0.0
    _kd: float = 0.0

    last_reading: float | None = None
    _state_fields: ClassVar[dict[str, Any]] = {"last_reading": float}

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

    def start(self, time: float) -> None:
        self.start_integral(time)

    def resume(self, time: float, reading: float, setpoint: float, correction: float) -> float:
        self.last_reading = reading
        return self.resume_integral(time, self._kp * (setpoint - reading), correction)

    def step(
        self, time: float, reading: float, setpoint: float, last_applied: float | None = None
    ) -> float:
        error = setpoint - reading
        dt = self.step_integral(error, time, last_applied)

        derivative = 0.0 if self.last_reading is None else (self.last_reading - reading) / dt
        output = self._kp * error + self.integral_value + self._kd * derivative

        self.last_reading = reading
        self.update_output(output)
        return output


OpenLoopTuning = Tuning("open_loop", OpenLoop.config())
