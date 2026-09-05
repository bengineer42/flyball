from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic.alias_generators import to_snake

from humctrl.clock import Duration, Rate, Time
from humctrl.controller import ControlLaw, ControlLawConfig
from humctrl.controller.types import ControlLawView, Tuning
from humctrl.pumps import BlendFlow
from humctrl.runners import HoldUntilHumidity, RampHumidityRunner, Runner, StartFrom, TestHumidities
from humctrl.typing import Percent, Positive, PositiveInt
from humctrl.utils import Labelled

if TYPE_CHECKING:
    from humctrl.manager import Manager


Commands: dict[str, type[Command]] = {}


class Command:
    """Base for everything a program can run.

    Subclassing registers the command under its tag. The wire model is built by
    the server layer, which is the only place that knows how a domain type
    crosses the wire.
    """

    tag: ClassVar[str] = ""

    def __init_subclass__(
        cls, tag: str | None = None, register: bool = True, **kwargs: Any
    ) -> None:
        super().__init_subclass__(**kwargs)
        cls.tag = tag or cls.__dict__.get("tag") or to_snake(cls.__name__)
        if not register:
            return
        clash = Commands.get(cls.tag)
        # ``@dataclass(slots=True)`` rebuilds the class, so this runs a second
        # time with a different object for the same command. Same qualified
        # name means the rebuild, not a clash.
        if clash is not None and (clash.__module__, clash.__qualname__) != (
            cls.__module__,
            cls.__qualname__,
        ):
            raise ValueError(f"tag {cls.tag!r} is already {clash.__name__}")
        Commands[cls.tag] = cls

    def __call__(self, manager: Manager) -> Runner | None:
        """Do the work, returning a runner if it has to be waited on."""
        ...


@dataclass(frozen=True)
class StartRecording(Command):
    name: str | None

    def __call__(self, manager: Manager) -> Runner | None:
        manager.start_recording(self.name)
        return None


@dataclass(frozen=True)
class StopRecording(Command):
    name: str | None

    def __call__(self, manager: Manager) -> Runner | None:
        manager.stop_recording(self.name)
        return None


@dataclass(frozen=True)
class AddFlag(Command):
    flag: str

    def __call__(self, manager: Manager) -> Runner | None:
        manager.add_recorder_flag(self.flag)
        return None


# region PumpCmds


@dataclass(frozen=True)
class SetBlend(Command):
    wet_fraction: float
    flow: BlendFlow

    def __call__(self, manager: Manager) -> Runner | None:
        manager.set_blend(self.flow, self.wet_fraction)
        return None


@dataclass(frozen=True)
class SetFlows(Command):
    dry: float
    wet: float

    def __call__(self, manager: Manager) -> Runner | None:
        manager.set_flows(dry=self.dry, wet=self.wet)
        return None


@dataclass(frozen=True)
class SetEfforts(Command):
    dry: float
    wet: float

    def __call__(self, manager: Manager) -> Runner | None:
        manager.set_efforts(dry=self.dry, wet=self.wet)
        return None


@dataclass(frozen=True)
class StopPumps(Command):
    def __call__(self, manager: Manager) -> Runner | None:
        manager.stop_pumps()
        return None


PumpCmds = (SetBlend, SetFlows, SetEfforts, StopPumps)


# endregion


@dataclass(frozen=True)
class StartController(Command):
    humidity: Percent
    flow: BlendFlow | None = None
    tuning: Tuning | ControlLaw | ControlLawConfig | ControlLawView | str | None = None

    def __call__(self, manager: Manager) -> Runner | None:
        manager.start_controller(self.humidity, self.flow, self.tuning)
        manager.apply()


@dataclass(frozen=True)
class UpdateSetpoint(Command):
    humidity: Percent

    def __call__(self, manager: Manager) -> Runner | None:
        manager.update_set_point(self.humidity)
        manager.apply()


class TargetMode(Labelled):
    """Which way the process humidity must move past the target to satisfy a hold."""

    ABOVE = "above", "Rises above the target"
    BELOW = "below", "Falls below the target"
    CROSS = "cross", "Crosses the target, either way"
    AT = "at", "Settles within tolerance"


@dataclass(frozen=True)
class RampHumidity(Command):
    target: Percent
    pace: Rate | Time | Duration
    flow: BlendFlow | None = None
    start: StartFrom | Percent = StartFrom.READING
    tuning: Tuning | ControlLaw | ControlLawConfig | str | None = None

    def __call__(self, manager: Manager) -> Runner | None:
        return RampHumidityRunner(
            manager,
            target=self.target,
            pace=self.pace,
            flow=self.flow,
            start_from=self.start,
            tuning=self.tuning,
        )


def less_than(value: float, target: float, tolerance: float) -> bool:
    return value < target - tolerance


def greater_than(value: float, target: float, tolerance: float) -> bool:
    return value > target + tolerance


def within_tolerance(value: float, target: float, tolerance: float) -> bool:
    return abs(value - target) <= tolerance


@dataclass(frozen=True)
class HoldConfig(Command):
    timeout: Positive | None
    min_duration: Duration | float = 0.0
    min_readings: PositiveInt = 1
    mode: TargetMode = TargetMode.CROSS
    tolerance: Percent = 0.0
    target: Percent | None = None

    def __call__(self, manager: Manager) -> Runner | None:
        target = self.target
        mode = self.mode

        if target is None:
            target = manager.required_target_humidity
        if mode is TargetMode.CROSS:
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

    def __init__(self, commands: list[Command]) -> None:
        self.commands = commands

    @property
    def running(self) -> bool:
        return self._running

    def suspend(self) -> None:
        self._running = False

    def run(self, manager: Manager, start: int = 0) -> None:
        self._running = True
        self._n = start
        while self._running and self._n < len(self.commands):
            manager.run_command(self.commands[self._n])
            self._n += 1
        self._running = False
