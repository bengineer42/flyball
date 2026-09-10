from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from humctrl.blender import BlenderState
from humctrl.clock import Duration, Rate
from humctrl.control import ValueSource
from humctrl.control.setpoint import LinearRampSetpoint, SetPointGenerator
from humctrl.control.types import ControlLawLike, Transfer
from humctrl.programmer.command import Activity, Command, CommandResult
from humctrl.pumps import BlendFlow
from humctrl.resource import Operator
from humctrl.state import State, View
from humctrl.typing import Percent, Positive, PositiveInt
from humctrl.utils import Labelled

from .activites import Sustained, TestHumidities

if TYPE_CHECKING:
    from humctrl.rig import Rig


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


@dataclass(frozen=True)
class SetFractionBlend(Command):
    wet_fraction: float
    flow: BlendFlow

    def run(self, rig: Rig, operator: Operator | None = None) -> BlenderState:
        return rig.set_fraction_blend(self.flow, self.wet_fraction, publish=False, by=operator)


@dataclass(frozen=True)
class SetBlend(Command):
    humidity: float
    flow: BlendFlow

    def run(self, rig: Rig, operator: Operator | None = None) -> BlenderState:
        return rig.set_blend(self.flow, self.humidity, publish=False, by=operator)


@dataclass(frozen=True)
class SetFlows(Command[BlenderState]):
    dry: float
    wet: float

    def run(self, rig: Rig, operator: Operator | None = None) -> BlenderState:
        return rig.set_flows(dry=self.dry, wet=self.wet, publish=False, by=operator)


@dataclass(frozen=True)
class SetEfforts(Command[BlenderState]):
    dry: float
    wet: float

    def run(self, rig: Rig, operator: Operator | None = None) -> BlenderState:
        return rig.set_efforts(dry=self.dry, wet=self.wet, publish=False, by=operator)


@dataclass(frozen=True)
class StopPumps(Command[BlenderState]):
    def run(self, rig: Rig, operator: Operator | None = None) -> BlenderState:
        return rig.stop_pumps(publish=False, by=operator)


PumpCmds = (SetBlend, SetFractionBlend, SetFlows, SetEfforts, StopPumps)


# endregion


@dataclass(frozen=True)
class RegulateHumidity(Command):
    at: ValueSource | Percent
    flow: BlendFlow | None = None
    generator: SetPointGenerator | None = None
    tuning: ControlLawLike | str | None = None
    transfer: Transfer | None = None

    def run(self, rig: Rig, operator: Operator | None = None) -> View:
        rig.regulate(
            self.at,
            flow=self.flow,
            generator=self.generator,
            tuning=self.tuning,
            transfer=self.transfer,
            by=operator,
        )
        return rig.view


@dataclass(frozen=True)
class UpdateSetpoint(Command[None]):
    humidity: Percent

    def run(self, rig: Rig, operator: Operator | None = None) -> None:
        rig.controller_reference(at=self.humidity, by=operator)


class TargetMode(Labelled):
    """Which way the process humidity must move past the target to satisfy a hold."""

    ABOVE = "above", "Rises above the target"
    BELOW = "below", "Falls below the target"
    CROSS = "cross", "Crosses the target, either way"
    AT = "at", "Settles within tolerance"


@dataclass(frozen=True)
class LinearRampHumidity(Command):
    end: Percent
    pace: Rate | Duration
    start: ValueSource | Percent = ValueSource.PROCESS

    def run(self, rig: Rig, operator: Operator | None = None) -> CommandResult[State]:

        generator = LinearRampSetpoint(self.pace, self.end)
        rig.controller_reference(at=self.start, generator=generator, publish=False, by=operator)

        return CommandResult.parse(rig.state, generator.signal)


def less_than(value: float, target: float, tolerance: float) -> bool:
    return value < target - tolerance


def greater_than(value: float, target: float, tolerance: float) -> bool:
    return value > target + tolerance


def within_tolerance(value: float, target: float, tolerance: float) -> bool:
    return abs(value - target) <= tolerance


@dataclass(frozen=True)
class Settle(Command):
    timeout: Positive | None
    min_duration: Duration | float = 0.0
    min_readings: PositiveInt = 1
    mode: TargetMode = TargetMode.CROSS
    tolerance: Percent = 0.0
    target: ValueSource | Percent = ValueSource.SETPOINT

    def run(self, rig: Rig) -> Activity:
        target = self.target
        mode = self.mode

        target = rig.resolve_value_source(target)
        match mode:
            case TargetMode.ABOVE:
                test = greater_than
            case TargetMode.BELOW:
                test = less_than
            case TargetMode.AT:
                test = within_tolerance
        return Sustained(
            TestHumidities(test, target, self.tolerance),
            timeout=self.timeout,
            min_duration=self.min_duration,
            min_readings=self.min_readings,
        )
