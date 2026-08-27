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

    def step(self, time: float, reading: float, set_point: float) -> float: ...


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

    def build(self) -> PIController:
        return PIController(self.kp, self.ki)


@control_law_config("PID")
class PIDControllerConfig(ControlLawConfig):
    kp: float = 0.0
    ki: float = 0.0
    kd: float = 0.0

    def build(self) -> PIDController:
        return PIDController(self.kp, self.ki, self.kd)


class PController(ControlLaw):
    kp: float

    def __init__(self, kp: float):
        self.kp = kp

    def step(self, time: float, reading: float, set_point: float) -> float:
        error = set_point - reading
        output = self.kp * error
        return output


class PIController(ControlLaw):
    kp: float = 0
    ki: float = 0

    integral: float = 0.0
    last_time: float

    def __init__(self, kp: float = 0, ki: float = 0):
        self.kp = kp
        self.ki = ki

    def start(
        self, time: float, reading: float, set_point: float, output: float | None = None
    ) -> None:
        self.last_time = time
        self.integral = 0.0
        if output is not None and self.ki:
            self.integral = (output - self.kp * (set_point - reading)) / self.ki

    def step(self, time: float, reading: float, set_point: float) -> float:
        error = set_point - reading
        if self.last_time is not None:
            dt = time - self.last_time
            assert dt > 0, ValueError("Time must be increasing")
            self.integral += error * dt

        output = self.kp * error + self.ki * self.integral

        self.last_time = time

        return output


class PIDController(ControlLaw):
    kp: float = 0
    ki: float = 0
    kd: float = 0

    integral: float = 0.0
    last_error: float = 0.0
    last_time: float

    def __init__(self, kp: float = 0, ki: float = 0, kd: float = 0):
        self.kp = kp
        self.ki = ki
        self.kd = kd

    def start(
        self, time: float, reading: float, set_point: float, output: float | None = None
    ) -> None:
        self.last_time = time
        self.last_error = set_point - reading
        self.integral = 0.0
        if output is not None and self.ki:
            self.integral = (output - self.kp * (set_point - reading)) / self.ki

    def step(self, time: float, reading: float, set_point: float) -> float:
        error = set_point - reading
        derivative = 0.0
        if self.last_time is not None:
            dt = time - self.last_time
            assert dt > 0, ValueError("Time must be increasing")
            self.integral += error * dt
            derivative = (error - self.last_error) / dt

        output = self.kp * error + self.ki * self.integral + self.kd * derivative

        self.last_error = error
        self.last_time = time

        return output
