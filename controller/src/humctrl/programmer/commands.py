from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from humctrl.control import (
    ControlLawLike,
    LinearRampSetpoint,
    Loop,
    SetPointGenerator,
    Transfer,
    ValueSource,
)
from humctrl.core import Channel, Duration, Operator, Percent, Positive, PositiveInt, Reading, Speed
from humctrl.core.signal import Signal
from humctrl.runtime import Rig

from .activites import Sustained
from .command import Activity, Command

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
    loop: str
    at: ValueSource | float
    generator: SetPointGenerator | None = None
    tuning: ControlLawLike | str | None = None
    transfer: Transfer = Transfer.TRACK

    def run(self, rig: Rig, operator: Operator | None = None) -> None:

        control_law = (
            rig.resolve_tuning(self.tuning) if isinstance(self.tuning, str) else self.tuning
        )
        loop = rig.resolve_loop(self.loop)
        loop.regulate(
            self.at,
            generator=self.generator,
            tuning=control_law,
            transfer=self.transfer,
        )


@dataclass(frozen=True)
class LinearRamp(Command):
    loop: str
    end: float
    pace: Speed | Duration
    start: ValueSource | float = ValueSource.PROCESS

    def run(self, rig: Rig, operator: Operator | None = None) -> Signal:

        generator = LinearRampSetpoint(self.pace, self.end)
        rig.resolve_loop(self.loop).set_reference(at=self.start, generator=generator)
        return generator.signal


@dataclass(frozen=True)
class UpdateSetpoint(Command):
    loop: str
    value: float

    def run(self, rig: Rig, operator: Operator | None = None) -> None:
        rig.resolve_loop(self.loop).set_reference(at=self.value)


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


class Sustain(Command, registered=True):
    channel: Channel
    timeout: Positive | None
    duration: Duration
    readings: PositiveInt = 1

    @abstractmethod
    def resolve_test(self, loop: Loop) -> Callable[[float], bool]: ...

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity:
        return Sustained(
            self.channel,
            SequentialPassesReadings(
                self.resolve_test(rig.resolve_loop(None)),
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
