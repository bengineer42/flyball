from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from humctrl.core import Speed

from humctrl.control import (
    ControlLawLike,
    LinearRampSetpoint,
    SetPointGenerator,
    Transfer,
    ValueSource,
)
from humctrl.core import Duration, Operator, Percent, Positive, PositiveInt, Rate, Reading
from humctrl.state import State, View

from .activites import Sustained
from .command import Activity, Command, CommandResult

# @dataclass(frozen=True)
# class StartRecording(Command):
#     name: str | None

#     def run(self, rig: Rig, operator: Operator | None = None) -> None:
#         rig.start_recording(self.name, by=operator)


# @dataclass(frozen=True)
# class StopRecording(Command):
#     name: str | None

#     def run(self, rig: Rig, operator: Operator | None = None) -> None:
#         rig.stop_recording(self.name, by=operator)


# @dataclass(frozen=True)
# class AddFlag(Command):
#     flag: str

#     def run(self, rig: Rig, operator: Operator | None = None) -> None:
#         rig.add_recorder_flag(self.flag, by=operator)


# region PumpCmds


# endregion


@dataclass(frozen=True)
class Regulate(Command):
    at: ValueSource | float
    generator: SetPointGenerator | None = None
    tuning: ControlLawLike | str | None = None
    transfer: Transfer | None = None

    def run(self, rig: Any, operator: Operator | None = None) -> View:
        rig.regulate(
            self.at,
            generator=self.generator,
            tuning=self.tuning,
            transfer=self.transfer,
            by=operator,
        )
        return rig.view


@dataclass(frozen=True)
class LinearRamp(Command):
    end: float
    pace: Speed | Duration
    start: ValueSource | float = ValueSource.PROCESS

    def run(self, rig: Any, operator: Operator | None = None) -> CommandResult[State]:

        generator = LinearRampSetpoint(self.pace, self.end)
        rig.controller_reference(at=self.start, generator=generator, publish=False, by=operator)

        return CommandResult.parse(rig.state, generator.signal)


@dataclass(frozen=True)
class UpdateSetpoint(Command[None]):
    value: float

    def run(self, rig: Any, operator: Operator | None = None) -> None:
        rig.controller_reference(at=self.value, by=operator)


class CriterionBase: ...


class Above(CriterionBase):
    margin: float = 0.0


class Below(CriterionBase):
    margin: float = 0.0


class Within(CriterionBase):
    tolerance: Positive = 0.0


type Criterion = Above | Below | Within


class GreaterThanTest:
    limit: float

    def __init__(self, target: Percent, margin: Percent) -> None:
        self.limit = target + margin

    def __call__(self, value: float) -> bool:
        return value > self.limit


class LessThanTest:
    limit: float

    def __init__(self, target: Percent, margin: Percent) -> None:
        self.limit = target - margin

    def __call__(self, value: float) -> bool:
        return value < self.limit


class WithinToleranceTest:
    def __init__(self, target: Percent, tolerance: Percent) -> None:
        self.target = target
        self.tolerance = tolerance

    def __call__(self, value: float) -> bool:
        return abs(value - self.target) <= self.tolerance


class SequentialPassesReadings:
    test: Callable[[float], bool]
    min_readings: PositiveInt
    min_duration_ns: int
    pass_time_ns: int = 0
    passed: int = 0

    def __init__(
        self,
        test: Callable[[float], bool],
        min_readings: PositiveInt = 1,
        min_duration_ns: int = 0,
    ) -> None:
        self.test = test
        self.min_readings = min_readings
        self.min_duration_ns = min_duration_ns

    def __call__(self, reading: Reading) -> bool:
        if self.passed == 0:
            self.pass_time_ns = reading.time_ns
        self.passed = self.passed + 1 if self.test(reading.value) else 0
        return (
            self.passed >= self.min_readings
            and reading.time_ns - self.pass_time_ns >= self.min_duration_ns
        )


class SustainTest

class Sustain(Command, registered=False):
    at: float | ValueSource
    test: Callable[[float], bool]
    timeout: Positive | None
    min_duration: Duration | float = 0.0
    min_readings: PositiveInt = 1

    def run(self, rig: Any, operator: Operator | None = None) -> Activity:
        value = rig.resolve_value_source(self.value)
        if isinstance(self.min_duration, Duration):
            min_duration_ns = self.min_duration.nanoseconds
        else:
            min_duration_ns = int(self.min_duration * 1e9)
        return Sustained(
            TestAllReadings(self.test, self.min_readings, min_duration_ns=min_duration_ns),
            timeout=self.timeout,
        )


class SettleValue(Command, registered=False):
    value: float | ValueSource
    test: Callable[[float], bool]
    timeout: Positive | None
    min_duration: Duration | float = 0.0
    min_readings: PositiveInt = 1

    def run(self, rig: Any, operator: Operator | None = None) -> Activity:
        value = rig.resolve_value_source(self.value)
        if isinstance(self.min_duration, Duration):
            min_duration_ns = self.min_duration.nanoseconds
        else:
            min_duration_ns = int(self.min_duration * 1e9)
        return Sustained(
            TestAllReadings(self.test, self.min_readings, min_duration_ns=min_duration_ns),
            timeout=self.timeout,
        )


class SettleAbove(SettleValue, registered=True):
    test: Callable[[float], bool]
    timeout: Positive | None
    min_duration: Duration | float = 0.0
    min_readings: PositiveInt = 1
