from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from humctrl.clock import Duration
from humctrl.programmer.command import Activity
from humctrl.readers import Reading
from humctrl.typing import Percent, Positive, PositiveInt

if TYPE_CHECKING:
    pass


# class Activity:
#     signal: Signal
#     error: Exception | None = None

#     def set_wait(self, timeout: float | None = None) -> None:
#         self._timeout = timeout
#         self._wait = Event()

#     def dwell(self) -> None:
#         if self._wait is not None:
#             self._wait.clear()
#             if self._timeout is not None:
#                 self._wait.wait(timeout=self._timeout)
#             if not self._wait.is_set():
#                 self.on_timeout()

#     def on_timeout(self) -> None:
#         return None

#     def step(self, rig: Rig, reading: Reading) -> None:
#         pass

#     def interrupt(self) -> None:
#         if self._wait is not None:
#             self._wait.set()
#         self.on_interrupt()

#     def on_interrupt(self) -> None:
#         return None


@dataclass(frozen=True, slots=True)
class TestHumidities:
    test: Callable[[Percent, Percent, Percent], bool]
    target: Percent
    tolerance: Percent = 0.0

    def __call__(self, readings: list[Reading]) -> bool:
        return all(self.test(reading.humidity, self.target, self.tolerance) for reading in readings)


class Sustained(Activity):
    def __init__(
        self,
        test: Callable[[list[Reading]], bool],
        timeout: Positive | None = None,
        min_duration: Duration | float = 0.0,
        min_readings: PositiveInt = 1,
    ) -> None:
        self._window: list[Reading] = []
        self._timeout = timeout
        self.min_duration = float(min_duration)
        self.min_readings = min_readings
        self.test = test

    def tick(self, reading: Reading) -> None:
        self._window.append(reading)
        start = reading.seconds - self.min_duration
        limit = len(self._window) - self.min_readings
        if limit < 0 or self._window[0].seconds > start:
            return
        i = next((i for i in range(limit, -1, -1) if self._window[i].seconds <= start), 0)

        self._window = self._window[i:]
        if self.test(self._window):
            self.finish()


# class WaitForGenerator(Activity):
#     end: Percent
#     rig: Rig

#     def __init__(self, rig: Rig, end: Percent, wait: Event | float | None) -> None:
#         self.end = end
#         self.rig = rig
#         if isinstance(wait, Event):
#             self._wait = wait
#         else:
#             self.set_wait(wait)

#     def on_timeout(self) -> None:
#         self.rig.update_setpoint(self.end)

#     def on_interrupt(self) -> None:
#         self.rig.remove_setpoint_generator()
