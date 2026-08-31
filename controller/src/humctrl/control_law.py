from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, Literal, Protocol, TypeVar, cast, dataclass_transform

from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from humctrl.utils import Config

CONFIG = ConfigDict(extra="forbid")
T = TypeVar("T")


class ControlLaw(Protocol):
    def start(
        self, time: float, reading: float, set_point: float, output: float | None = None
    ) -> None:
        pass

    def step(
        self, time: float, reading: float, set_point: float, last_applied: float | None = None
    ) -> float: ...


class ControlLawConfig(Config[ControlLaw]):
    type: str

    def build(self) -> ControlLaw:
        raise NotImplementedError


@dataclass_transform(frozen_default=True, field_specifiers=(Field,))
def control_law_config(tag: str) -> Callable[[type[T]], type[T]]:
    def wrap(cls: type[T]) -> type[T]:
        cls.__annotations__ = {**inspect.get_annotations(cls), "type": Literal[tag]}
        cast(Any, cls).type = Field(default=tag, repr=False)
        return cast(type[T], dataclass(frozen=True, config=CONFIG)(cls))

    return wrap


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


class PController(ControlLaw):
    kp: float

    def __init__(self, kp: float):
        self.kp = kp

    def step(
        self, time: float, reading: float, set_point: float, last_applied: float | None = None
    ) -> float:
        error = set_point - reading
        output = self.kp * error
        return output


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

    def set_ki_tt(self, ki: float, tt: float = 0.0) -> None:
        self._ki = ki
        self._tt_mul_ki = tt * ki

    def start_integral(
        self,
        time: float,
        reading: float,
        set_point: float,
        kp: float = 0.0,
        output: float | None = None,
    ) -> None:
        self.last_time = time
        self.integral = 0.0
        self.last_raw = output
        if output is not None and self._ki:
            self.integral = (output - kp * (set_point - reading)) / self._ki

    def step_integral(self, error: float, time: float, last_applied: float | None = None) -> float:
        dt = time - self.last_time

        if dt <= 0:
            raise ValueError("Time must be increasing")

        self.integral += error * dt
        if self._tt_mul_ki and last_applied is not None and self.last_raw is not None:
            self.integral += (last_applied - self.last_raw) * dt / self._tt_mul_ki

        self.last_time = time

        return dt

    def update_output(self, output: float):
        self.last_raw = output


class PIController(ControlLaw, IComponent):
    _kp: float = 0.0

    def __init__(self, kp: float = 0, ki: float = 0, tt: float = 0):
        self._kp = kp
        self.set_ki_tt(ki, tt)

    @property
    def kp(self) -> float:
        return self._kp

    def start(
        self, time: float, reading: float, set_point: float, output: float | None = None
    ) -> None:
        self.start_integral(time, reading, set_point, self._kp, output)

    def step(
        self, time: float, reading: float, set_point: float, last_applied: float | None = None
    ) -> float:
        error = set_point - reading
        self.step_integral(error, time, last_applied)
        output = self._kp * error + self.integral_value
        self.update_output(output)
        return output


class PIDController(ControlLaw, IComponent):
    _kp: float = 0.0
    _kd: float = 0.0

    last_reading: float = 0.0

    def __init__(self, kp: float = 0, ki: float = 0, kd: float = 0, tt: float = 0.0):
        self._kp = kp
        self._kd = kd
        self.set_ki_tt(ki, tt)

    @property
    def kp(self) -> float:
        return self._kp

    @property
    def kd(self) -> float:
        return self._kd

    def start(
        self, time: float, reading: float, set_point: float, output: float | None = None
    ) -> None:

        self.last_reading = reading
        self.start_integral(time, reading, set_point, self._kp, output)

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
