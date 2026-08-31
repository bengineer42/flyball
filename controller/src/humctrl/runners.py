from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from threading import Event
from typing import Protocol

from humctrl.clock import Duration, Rate, Time
from humctrl.manager import Manager
from humctrl.sensors import Reading
from humctrl.typing import Percent, Positive, PositiveInt


class Runner(Protocol):
    def hold(self): ...
    def step(self, reading: Reading): ...
    def interrupt(self): ...


@dataclass(frozen=True, slots=True)
class TestHumidities:
    test: Callable[[Percent, Percent, Percent], bool]
    target: Percent
    tolerance: Percent = 0.0

    def __call__(self, readings: list[Reading]) -> bool:
        return all(self.test(reading.humidity, self.target, self.tolerance) for reading in readings)


class HoldUntilHumidity(Runner):
    readings: list[Reading]
    wait: Event = Event()
    min_duration: float
    min_readings: PositiveInt
    timeout: Positive | None
    test: Callable[[list[Reading]], bool]

    def __init__(
        self,
        test: Callable[[list[Reading]], bool],
        timeout: Positive | None = None,
        min_duration: Duration | float = 0.0,
        min_readings: PositiveInt = 1,
    ):
        self.readings: list[Reading] = []
        self.timeout = timeout
        self.min_duration = float(min_duration)
        self.min_readings = min_readings
        self.test = test

    def hold(self):
        self.wait.clear()
        if self.timeout is not None:
            self.wait.wait(timeout=self.timeout)

    def step(self, reading: Reading):
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

    def interrupt(self):
        self.wait.set()


class StartFrom(Enum):
    READING = "reading"
    TARGET = "target"


class RampHumidity(Runner):
    target: Percent
    _wait: Event = Event()
    end_time: float
    rate: float

    def __init__(
        self,
        manager: Manager,
        target: Percent,
        pace: Rate | Time | Duration,
        start_from: StartFrom | Percent = StartFrom.READING,
    ):
        self.manager = manager
        self.target = target
        if isinstance(start_from, StartFrom) and start_from == StartFrom.TARGET:
            start_humidity = manager.required_regulated_humidity
        else:
            start_humidity = (
                manager.required_process_humidity
                if isinstance(start_from, StartFrom)
                else start_from
            )
            manager.start_regulating(start_humidity)
        now = manager.time()
        match pace:
            case Rate(per_second=per_second):
                self._end_time = now + abs(self.target - start_humidity) / per_second
            case Duration(as_seconds=seconds):
                self._end_time = now + seconds
            case Time(as_seconds=end_time):
                self._end_time = end_time
        self._rate = (self.target - start_humidity) / (self._end_time - now)

    def step(self, reading: Reading):
        now = self.manager.time()
        dt = self._end_time - now
        if dt <= 0:
            self.manager.update_regulating(self.target)
            self._wait.set()
        else:
            self.manager.update_regulating(self.target - dt * self._rate)

    def hold(self):
        wait_time = self._end_time - self.manager.time()
        if wait_time > 0:
            self._wait.wait(timeout=wait_time)
        if self._wait.is_set():
            self.manager.update_regulating(self.target)

    def interrupt(self):
        self._wait.set()
