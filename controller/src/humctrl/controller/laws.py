from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import cached_property
from typing import Any, Literal, TypeVar, cast, dataclass_transform

from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from .types import ControlLaw, ControlLawConfig

CONFIG = ConfigDict(extra="forbid")
T = TypeVar("T")


CONTROL_LAWS: dict[str, type[ControlLaw]] = {}


def control_law(tag: str) -> Callable[[type[T]], type[T]]:
    """Tag a control law with the ``type`` of its config, and register it.

    Lets a running law report what it is (``law.type``) and lets a tag be
    resolved back to a class (``CONTROL_LAWS[tag]``) without a match statement.
    """

    def wrap(cls: type[T]) -> type[T]:
        cast(Any, cls).type = tag
        CONTROL_LAWS[tag] = cast("type[ControlLaw]", cls)
        return cls

    return wrap


@dataclass_transform(frozen_default=True, field_specifiers=(Field,))
def control_law_config(tag: str) -> Callable[[type[T]], type[T]]:
    def wrap(cls: type[T]) -> type[T]:
        cls.__annotations__ = {**inspect.get_annotations(cls), "type": Literal[tag]}
        cast(Any, cls).type = Field(default=tag, repr=False)
        return cast(type[T], dataclass(frozen=True, config=CONFIG)(cls))

    return wrap


@control_law_config("open_loop")
class OpenLoopLaw(ControlLaw, ControlLawConfig):
    def __init__(self) -> None:
        pass

    def step(
        self, time: float, reading: float, set_point: float, last_applied: float | None = None
    ) -> float:
        return 0.0

    def build(self) -> OpenLoopLaw:
        return self

    @property
    def config(self) -> OpenLoopLaw:
        return self


OPEN_LOOP_LAW = OpenLoopLaw()


@control_law_config("P")
class PControllerConfig(ControlLawConfig):
    kp: float

    def build(self) -> PController:
        return PController(self.kp)


@control_law_config("PI")
class PIControllerConfig(ControlLawConfig):
    kp: float = 0.0
    ki: float = 0.0
    tt: float = 0.0

    def build(self) -> PIController:
        return PIController(self.kp, self.ki, self.tt)


@control_law_config("PID")
class PIDControllerConfig(ControlLawConfig):
    kp: float = 0.0
    ki: float = 0.0
    kd: float = 0.0
    tt: float = 0.0

    def build(self) -> PIDController:
        return PIDController(self.kp, self.ki, self.kd, self.tt)


@control_law("P")
class PController(ControlLaw):
    kp: float

    def __init__(self, kp: float) -> None:
        self.kp = kp

    def resume(self, time: float, reading: float, set_point: float, correction: float) -> float:
        """A proportional law has no memory, so it cannot hold ``correction``."""
        return self.kp * (set_point - reading)

    def step(
        self, time: float, reading: float, set_point: float, last_applied: float | None = None
    ) -> float:
        error = set_point - reading
        output = self.kp * error
        return output

    @property
    def config(self) -> ControlLawConfig:
        return PControllerConfig(kp=self.kp)


@dataclass(slots=True, frozen=True)
class IState:
    integral: float = 0.0
    last_raw: float | None = None
    last_time: float = 0.0


class IComponent:
    _ki: float = 0.0
    _tt_mul_ki: float = 0.0

    integral: float = 0.0
    last_raw: float | None = None
    last_time: float = 0.0

    @property
    def ki(self) -> float:
        return self._ki

    @property
    def tt(self) -> float:
        return self._tt_mul_ki / self._ki if self._ki else 0.0

    @property
    def integral_value(self) -> float:
        return self.integral * self.ki

    @property
    def state(self) -> IState:
        return IState(
            integral=self.integral,
            last_raw=self.last_raw,
            last_time=self.last_time,
        )

    def set_ki_tt(self, ki: float, tt: float = 0.0) -> None:
        self._ki = ki
        self._tt_mul_ki = tt * ki

    def start_integral(self, time: float) -> None:
        """Reset the integrator. ``last_raw`` stays unset so the first step
        skips the anti-windup term, which has no previous output to compare."""
        self.last_time = time
        self.integral = 0.0
        self.last_raw = None

    def resume_integral(self, time: float, kp_term: float, correction: float) -> float:
        """Seed the integrator so the next output is ``correction``.

        ``kp_term`` is the law's proportional contribution at the resume instant,
        which the integral has to cancel. Returns the correction actually seeded:
        with no integral there is nothing to hold the offset, so the law resumes
        at ``kp_term`` however much was asked for.

        ``last_raw`` is set to whatever is returned rather than left unset, so the
        first step's anti-windup term measures against the output the law really
        is resuming from.
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


@control_law("PI")
class PIController(IComponent, ControlLaw):
    _kp: float = 0.0

    def __init__(self, kp: float = 0, ki: float = 0, tt: float = 0) -> None:
        self._kp = kp
        self.set_ki_tt(ki, tt)

    @property
    def kp(self) -> float:
        return self._kp

    def start(self, time: float, reading: float) -> None:
        self.start_integral(time)

    def resume(self, time: float, reading: float, set_point: float, correction: float) -> float:
        return self.resume_integral(time, self._kp * (set_point - reading), correction)

    def step(
        self, time: float, reading: float, set_point: float, last_applied: float | None = None
    ) -> float:
        error = set_point - reading
        self.step_integral(error, time, last_applied)
        output = self._kp * error + self.integral_value
        self.update_output(output)
        return output

    @cached_property
    def config(self) -> PIControllerConfig:
        return PIControllerConfig(kp=self._kp, ki=self._ki, tt=self.tt)


@dataclass(slots=True, frozen=True)
class PIDState(IState):
    last_reading: float = 0.0


@control_law("PID")
class PIDController(ControlLaw, IComponent):
    _kp: float = 0.0
    _kd: float = 0.0

    last_reading: float = 0.0

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

    @property
    def state(self) -> PIDState:
        return PIDState(
            integral=self.integral,
            last_raw=self.last_raw,
            last_time=self.last_time,
            last_reading=self.last_reading,
        )

    def start(self, time: float, reading: float) -> None:
        self.last_reading = reading
        self.start_integral(time)

    def resume(self, time: float, reading: float, set_point: float, correction: float) -> float:
        self.last_reading = reading
        return self.resume_integral(time, self._kp * (set_point - reading), correction)

    def step(
        self, time: float, reading: float, set_point: float, last_applied: float | None = None
    ) -> float:
        error = set_point - reading
        dt = self.step_integral(error, time, last_applied)

        derivative = (self.last_reading - reading) / dt
        output = self._kp * error + self.integral_value + self._kd * derivative

        self.last_reading = reading
        self.update_output(output)
        return output

    @cached_property
    def config(self) -> ControlLawConfig:
        return PIDControllerConfig(kp=self._kp, ki=self._ki, kd=self._kd, tt=self.tt)
