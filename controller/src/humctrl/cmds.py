from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from humctrl.clock import Duration, Rate, Time
from humctrl.control_law import ControlLaw, ControlLawConfig
from humctrl.manager import Manager
from humctrl.pumps import BlendFlow, OnOverdrive
from humctrl.runners import HoldUntilHumidity, RampHumidity, Runner, StartFrom, TestHumidities
from humctrl.typing import Percent, Positive, PositiveInt


class Command(Protocol):
    def __call__(self, manager: Manager) -> Runner | None: ...


@dataclass(frozen=True, slots=True)
class SetControlLaw(Command):
    control_law: ControlLaw | ControlLawConfig

    def __call__(self, manager: Manager):
        manager.set_control_law(self.control_law)


@dataclass(frozen=True, slots=True)
class StartRecording(Command):
    name: str | None

    def __call__(self, manager: Manager):
        manager.start_recording(self.name)


@dataclass(frozen=True, slots=True)
class StopRecording(Command):
    name: str | None

    def __call__(self, manager: Manager):
        manager.stop_recording(self.name)


@dataclass(frozen=True, slots=True)
class AddFlag(Command):
    flag: str

    def __call__(self, manager: Manager):
        manager.add_recorder_flag(self.flag)


# region PumpCmds


@dataclass(frozen=True, slots=True)
class SetBlend(Command):
    wet_fraction: float
    flow: BlendFlow | float
    on_overdrive: OnOverdrive | None = None

    def __call__(self, manager: Manager):
        manager.set_blend(self.flow, self.wet_fraction, self.on_overdrive)


@dataclass(frozen=True, slots=True)
class SetBlendFlow(Command):
    flow: BlendFlow | float
    on_overdrive: OnOverdrive | None = None

    def __call__(self, manager: Manager):
        manager.set_blend_flow(self.flow, self.on_overdrive)


@dataclass(frozen=True, slots=True)
class SetWetFraction(Command):
    wet_fraction: float
    on_overdrive: OnOverdrive | None = None

    def __call__(self, manager: Manager):
        manager.set_wet_fraction(self.wet_fraction, self.on_overdrive)


@dataclass(frozen=True, slots=True)
class SetDryFraction(Command):
    dry_fraction: float
    on_overdrive: OnOverdrive | None = None

    def __call__(self, manager: Manager):
        manager.set_dry_fraction(self.dry_fraction, self.on_overdrive)


@dataclass(frozen=True, slots=True)
class SetFlows(Command):
    wet: float
    dry: float

    def __call__(self, manager: Manager):
        manager.set_flows(self.wet, self.dry)


@dataclass(frozen=True, slots=True)
class SetEfforts(Command):
    wet: float
    dry: float

    def __call__(self, manager: Manager):
        manager.set_efforts(self.wet, self.dry)


@dataclass(frozen=True, slots=True)
class StopPumps(Command):
    def __call__(self, manager: Manager):
        manager.stop_pumps()


PumpCmds = (SetBlend, SetBlendFlow, SetWetFraction, SetDryFraction, SetFlows, SetEfforts, StopPumps)


# endregion


@dataclass(frozen=True, slots=True)
class StartRegulation(Command):
    humidity: Percent

    def __call__(self, manager: Manager):
        manager.start_regulating(self.humidity)


@dataclass(frozen=True, slots=True)
class UpdateRegulation(Command):
    humidity: Percent

    def __call__(self, manager: Manager):
        manager.update_regulating(self.humidity)


class TargetMode(Enum):
    ABOVE = "above"
    BELOW = "below"
    CROSS = "cross"
    AT = "at"


@dataclass(frozen=True, slots=True)
class RampHumidityConfig(Command):
    target: Percent
    pace: Rate | Time | Duration
    start_from: StartFrom | Percent = StartFrom.READING

    def __call__(self, manager: Manager) -> RampHumidity:
        return RampHumidity(
            manager,
            target=self.target,
            pace=self.pace,
            start_from=self.start_from,
        )


def less_than(value: float, target: float, tolerance: float) -> bool:
    return value < target - tolerance


def greater_than(value: float, target: float, tolerance: float) -> bool:
    return value > target + tolerance


def within_tolerance(value: float, target: float, tolerance: float) -> bool:
    return abs(value - target) <= tolerance


@dataclass(frozen=True, slots=True)
class HoldConfig(Command):
    timeout: Positive | None
    min_duration: Duration | float = 0.0
    min_readings: PositiveInt = 1
    mode: TargetMode = TargetMode.CROSS
    tolerance: Percent = 0.0
    target: Percent | None = None

    def __call__(self, manager: Manager) -> Runner:
        target = self.target
        mode = self.mode

        if target is None:
            target = manager.required_target_humidity
        if mode == TargetMode.CROSS:
            if manager.required_process_humidity > target:
                mode = TargetMode.BELOW
            else:
                mode = TargetMode.ABOVE
        match mode:
            case TargetMode.ABOVE:
                test = greater_than
            case TargetMode.BELOW:
                test = less_than
            case TargetMode.AT:
                test = within_tolerance

        return HoldUntilHumidity(
            TestHumidities(test, target, self.tolerance),
            timeout=self.timeout,
            min_duration=self.min_duration,
            min_readings=self.min_readings,
        )


class ControlProgram:
    commands: list[Command]
    _n: int = 0
    _running: bool = False

    def __init__(self, commands: list[Command]):
        self.commands = commands

    def running(self) -> bool:
        return self._running

    def run(self, manager: Manager, start: int = 0):
        self._running = True
        self._n = start
        while self._running and self._n < len(self.commands):
            manager._run_command(self.commands[self._n])
            self._n += 1
        self._running = False
