from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from humctrl.control import (
    ControlLawLike,
    LinearRampSetpoint,
    Loop,
    SetPointGenerator,
    Transfer,
    ValueSource,
)
from humctrl.core import Duration, Operator, Percent, Positive, PositiveInt, Reading, Speed
from humctrl.state import State, View

from .activites import Sustained
from .command import Command, CommandResult, LoopActivity, LoopCommand

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
    readings: PositiveInt
    duration_ns: int
    first_time_ns: int = 0
    passed: int = 0

    def __init__(
        self,
        test: Callable[[float], bool],
        readings: PositiveInt = 1,
        duration_ns: int = 0,
    ) -> None:
        self.test = test
        self.readings = readings
        self.duration_ns = duration_ns

    def __call__(self, reading: Reading) -> bool:
        if self.passed == 0:
            self.first_time_ns = reading.time_ns
        self.passed = self.passed + 1 if self.test(reading.value) else 0
        return (
            self.passed >= self.readings
            and reading.time_ns - self.first_time_ns >= self.duration_ns
        )


class Sustain(LoopCommand, registered=True):
    timeout: Positive | None
    duration: Duration
    readings: PositiveInt = 1

    @abstractmethod
    def resolve_test(self, loop: Loop) -> Callable[[float], bool]: ...

    def run_on_loop(self, loop: Loop, operator: Operator | None = None) -> LoopActivity:
        return Sustained(
            SequentialPassesReadings(
                self.resolve_test(loop),
                readings=self.readings,
                duration_ns=self.duration.nanoseconds,
            ),
            timeout=self.timeout,
        )


class SettleAbove(Sustain):
    above: float | ValueSource
    margin: float = 0.0
    timeout: Positive | None
    min_duration: Duration | float = 0.0
    min_readings: PositiveInt = 1

    def resolve_test(self, loop: Loop) -> Callable[[float], bool]:
        return GreaterThanTest(loop.resolve_value(self.above), self.margin)


class SettleBelow(Sustain):
    below: float | ValueSource
    margin: float = 0.0
    timeout: Positive | None
    min_duration: Duration | float = 0.0
    min_readings: PositiveInt = 1

    def resolve_test(self, loop: Loop) -> Callable[[float], bool]:
        return LessThanTest(loop.resolve_value(self.below), self.margin)


class SettleAt(Sustain):
    at: float | ValueSource
    tolerance: float
    timeout: Positive | None
    min_duration: Duration | float = 0.0
    min_readings: PositiveInt = 1

    def resolve_test(self, loop: Loop) -> Callable[[float], bool]:
        return WithinToleranceTest(loop.resolve_value(self.at), self.tolerance)
