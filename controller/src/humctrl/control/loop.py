from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import NamedTuple, Protocol, Self

from humctrl.control.errors import LastReadingNotAvailableError
from humctrl.core import Reading
from humctrl.core.clock import Clock
from humctrl.core.utils import require

from .controller import Controller
from .setpoint import SetPointGenerator
from .types import (
    ControlLaw,
    ControlLawConfig,
    ControlLawLike,
    ControlLawView,
    Transfer,
    Tuning,
    ValueSource,
)


class Actuator(Protocol):
    def set_demand(self, demand: float) -> float | None: ...


class ApplyResult(NamedTuple):
    expected: float | None = None
    last_demand: float | None = None


class RegulateResult(NamedTuple):
    expected: float | None = None
    last_demand: float | None = None
    bump: float | None = None


class Loop[A: Actuator]:
    clock: Clock
    actuator: A
    expected: float | None = None
    last_demand: float | None = None
    controller: Controller
    generator: SetPointGenerator | None = None
    reading: Reading | None = None
    regulating: bool = False
    _on_tick: dict[Callable[[Self, Reading | None], None], None]
    lock: RLock

    def __init__(
        self,
        clock: Clock,
        law: ControlLaw | ControlLawConfig | ControlLawView | Tuning,
        actuator: A,
    ) -> None:
        self.clock = clock
        self.actuator = actuator
        self.controller = Controller(law)
        self.last_time_ns = self.clock.now_ns()
        self.lock = RLock()
        self._on_tick = {}

    @property
    def last_value(self) -> float | None:
        return self.reading and self.reading.value

    @property
    def required_last_value(self) -> float:
        return require(self.last_value, LastReadingNotAvailableError)

    @property
    def last_time(self) -> float | None:
        return self.reading and self.reading.seconds

    def resolve_value(self, at: ValueSource | float, time_ns: int | None = None) -> float:
        if at is ValueSource.PROCESS:
            return self.required_last_value
        if at is ValueSource.SETPOINT:
            return self.controller.setpoint_at(self.get_time_ns(time_ns))
        if at is ValueSource.DEMAND:
            return self.controller.demand_at(self.get_time_ns(time_ns))
        return float(at)

    def regulate(
        self,
        at: ValueSource | float,
        generator: SetPointGenerator | None = None,
        tuning: ControlLawLike | None = None,
        transfer: Transfer = Transfer.TRACK,
        time_ns: int | None = None,
    ) -> RegulateResult:

        time_ns = self.get_time_ns(time_ns)
        self.controller.set_reference(self.resolve_value(at, time_ns), time_ns, generator)
        bump = self.controller.transfer(time_ns, tuning, transfer, self.expected)
        return RegulateResult(*self.apply(time_ns), bump=bump)

    def get_time_ns(self, time_ns: int | None = None) -> int:
        return self.clock.now_ns() if time_ns is None else time_ns

    def _set_reference(
        self,
        at: float,
        time_ns: int,
        generator: SetPointGenerator | None = None,
    ) -> ApplyResult:
        self.controller.set_reference(at, time_ns, generator)
        return self.apply(time_ns)

    def set_reference(
        self,
        at: float | ValueSource,
        time_ns: int | None = None,
        generator: SetPointGenerator | None = None,
    ) -> ApplyResult:
        time_ns = self.get_time_ns(time_ns)
        return self._set_reference(self.resolve_value(at, time_ns), time_ns, generator=generator)

    def attach_on_tick(self, callback: Callable[[Self, Reading], None] | None) -> None:
        with self.lock:
            if callback is not None:
                self._on_tick[callback] = None

    def detach_on_tick(self, callback: Callable[[Self, Reading], None] | None) -> None:
        with self.lock:
            if callback is not None:
                self._on_tick.pop(callback)

    def _run_on_tick(self, reading: Reading | None) -> None:
        for callback in self._on_tick:
            callback(self, reading)

    def tick(self, reading: Reading | None) -> None:
        if reading is not None:
            self.reading = reading
        self._run_on_tick(reading)
        if self.regulating:
            self._step(reading)

    def _step(self, reading: Reading) -> ApplyResult:
        self.controller.step(reading.time_ns, reading.value)
        return self.apply(reading.time_ns)

    def apply(self, time_ns: int | None = None) -> ApplyResult:
        time = self.get_time_ns(time_ns)
        self.last_demand = self.controller.demand_at(time)
        self.expected = self.actuator.set_demand(self.last_demand)
        return ApplyResult(expected=self.expected, last_demand=self.last_demand)
