"""Devices and builders shared by the suite. Not a conftest: imported once, by name."""

from __future__ import annotations

from dataclasses import dataclass

from flyball.core.reading import Measurand, Sample, Source
from flyball.core.sink import Actuator, ActuatorState, command


@dataclass(frozen=True, slots=True, kw_only=True)
class DutyState(ActuatorState):
    duty: float = 0.0


class DutyHeater(Actuator):
    """A heater with one command, for device and route tests."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.duty = 0.0
        self._demand: float | None = None

    def set_demand(self, demand: float) -> float | None:
        self._demand = demand
        return None

    @property
    def state(self) -> DutyState:
        return DutyState(demand=self._demand, duty=self.duty)

    @command
    def set_duty(self, duty: float, ramp_s: float = 0.0) -> DutyState:
        """Drive the element at a fixed duty."""
        self.duty = duty
        return self.state

    @command(tag="off")
    def switch_off(self) -> None:
        """Stop heating."""
        self.duty = 0.0


def sample(
    source: Source, measurand: Measurand, value: float, time_ns: int, seq: int = 1
) -> Sample:
    return Sample(source, seq, time_ns, {measurand: value})
