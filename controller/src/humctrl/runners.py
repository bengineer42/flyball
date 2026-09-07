from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import TYPE_CHECKING

from humctrl.clock import Duration
from humctrl.readers import Reading
from humctrl.typing import Percent, Positive, PositiveInt
from humctrl.utils import Labelled

if TYPE_CHECKING:
    from humctrl.manager import Manager


class Runner:
    _timeout: float | None = None
    _wait: Event | None = None

    def setup(self) -> None:
        self._wait = Event()

    def set_wait(self, timeout: float | None = None) -> None:
        self._timeout = timeout
        self._wait = Event()

    def dwell(self) -> None:
        if self._wait is not None:
            self._wait.clear()
            if self._timeout is not None:
                self._wait.wait(timeout=self._timeout)
            if not self._wait.is_set():
                self.on_timeout()

    def on_timeout(self) -> None:
        return None

    def step(self, reading: Reading) -> None:
        pass

    def interrupt(self) -> None:
        if self._wait is not None:
            self._wait.set()
        self.on_interrupt()

    def on_interrupt(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class TestHumidities:
    test: Callable[[Percent, Percent, Percent], bool]
    target: Percent
    tolerance: Percent = 0.0

    def __call__(self, readings: list[Reading]) -> bool:
        return all(self.test(reading.humidity, self.target, self.tolerance) for reading in readings)


class HoldUntilHumidity(Runner):
    readings: list[Reading]
    min_duration: float
    min_readings: PositiveInt
    test: Callable[[list[Reading]], bool]

    def __init__(
        self,
        test: Callable[[list[Reading]], bool],
        timeout: Positive | None = None,
        min_duration: Duration | float = 0.0,
        min_readings: PositiveInt = 1,
    ) -> None:
        self.readings: list[Reading] = []
        self._timeout = timeout
        self.min_duration = float(min_duration)
        self.min_readings = min_readings
        self.test = test

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
                self.interrupt()


class StartFrom(Labelled):
    """Where a ramp begins."""

    READING = "reading", "The current reading"
    TARGET = "target", "The current target"


class WaitForGenerator(Runner):
    end: Percent
    manager: Manager

    def __init__(self, manager: Manager, end: Percent, duration: float) -> None:
        self.end = end
        self.manager = manager
        self.set_wait(duration)

    def on_timeout(self) -> None:
        self.manager.update_set_point(self.end)

    def on_interrupt(self) -> None:
        self.manager.remove_set_point_generator()
