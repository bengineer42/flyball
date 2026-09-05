from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import TYPE_CHECKING, Protocol

from humctrl.clock import Duration, Rate, Time
from humctrl.controller import ControlLaw, ControlLawConfig
from humctrl.controller.types import Tuning
from humctrl.pumps import BlendFlow
from humctrl.readers import Reading
from humctrl.typing import Percent, Positive, PositiveInt
from humctrl.utils import Labelled

if TYPE_CHECKING:
    from humctrl.manager import Manager


class Runner(Protocol):
    _suspended: bool = False

    @property
    def suspended(self) -> bool:
        return self._suspended

    def hold(self) -> None: ...
    def step(self, reading: Reading) -> None: ...
    def suspend(self) -> None:
        self._suspended = True

    def resume(self) -> None:
        self._suspended = False

    def interrupt(self) -> None: ...


@dataclass(frozen=True, slots=True)
class TestHumidities:
    test: Callable[[Percent, Percent, Percent], bool]
    target: Percent
    tolerance: Percent = 0.0

    def __call__(self, readings: list[Reading]) -> bool:
        return all(self.test(reading.humidity, self.target, self.tolerance) for reading in readings)


class HoldUntilHumidity(Runner):
    readings: list[Reading]
    wait: Event
    min_duration: float
    min_readings: PositiveInt
    timeout: Positive | None
    test: Callable[[list[Reading]], bool]
    _suspend_time: float | None = None

    def __init__(
        self,
        test: Callable[[list[Reading]], bool],
        timeout: Positive | None = None,
        min_duration: Duration | float = 0.0,
        min_readings: PositiveInt = 1,
    ) -> None:
        self.wait = Event()
        self.readings: list[Reading] = []
        self.timeout = timeout
        self.min_duration = float(min_duration)
        self.min_readings = min_readings
        self.test = test

    def hold(self) -> None:
        self.wait.clear()
        if self.timeout is not None:
            self.wait.wait(timeout=self.timeout)

    def suspend(self) -> None:
        self._suspended = True
        self._suspend_time = time.monotonic()

    def step(self, reading: Reading) -> None:
        self.readings.append(reading)
        if (
            len(self.readings) >= self.min_readings
            and self.readings[-1].time - self.readings[0].time >= self.min_duration
        ):
            while (
                len(self.readings) > self.min_readings
                and self.readings[-1].time - self.readings[0].time > self.min_duration
            ):
                self.readings.pop(0)
            if self.test(self.readings):
                self.wait.set()

    def interrupt(self) -> None:
        self.wait.set()


class StartFrom(Labelled):
    """Where a ramp begins."""

    READING = "reading", "The current reading"
    TARGET = "target", "The current target"


class RampHumidityRunner(Runner):
    target: Percent
    wait: Event
    end_time: float
    rate: float

    def __init__(
        self,
        manager: Manager,
        target: Percent,
        pace: Rate | Time | Duration,
        flow: BlendFlow | None = None,
        start_from: StartFrom | Percent = StartFrom.READING,
        tuning: Tuning | ControlLaw | ControlLawConfig | str | None = None,
    ) -> None:
        self.manager = manager
        self.target = target
        self.wait = Event()
        if isinstance(start_from, StartFrom) and start_from == StartFrom.TARGET:
            start_humidity = manager.required_target_humidity
        else:
            start_humidity = (
                manager.required_process_humidity
                if isinstance(start_from, StartFrom)
                else start_from
            )
        if tuning is None and manager.controller is not None:
            manager.update_set_point(start_humidity)
        else:
            manager.start_controller(start_humidity, flow, tuning=tuning)

        now = manager.elapsed_s()
        match pace:
            case Rate(per_second=per_second):
                self.end_time = now + abs(self.target - start_humidity) / per_second
            case Duration(seconds=seconds):
                self.end_time = now + seconds
            case Time(seconds=end_time):
                self.end_time = end_time
        self.rate = (self.target - start_humidity) / (self.end_time - now)

    def step(self, reading: Reading) -> None:
        now = self.manager.elapsed_s()
        dt = self.end_time - now
        if dt <= 0:
            self.manager.update_set_point(self.target)
            self.wait.set()
        else:
            self.manager.update_set_point(self.target - dt * self.rate)

    def hold(self) -> None:
        wait_time = self.end_time - self.manager.elapsed_s()
        if wait_time > 0:
            self.wait.wait(timeout=wait_time)
        if self.wait.is_set():
            self.manager.update_set_point(self.target)

    def interrupt(self) -> None:
        self.wait.set()
