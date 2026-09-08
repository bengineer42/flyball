from typing import NamedTuple, Protocol

from humctrl.controller import Controller, SetPointGenerator
from humctrl.controller.types import (
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


class Loop[A: Actuator](Controller):
    actuator: A
    expected: float | None = None
    last_demand: float | None = None

    def __init__(
        self,
        law: ControlLaw | ControlLawConfig | ControlLawView | Tuning,
        setpoint: float,
        time: float,
        actuator: A,
    ) -> None:
        self.actuator = actuator
        super().__init__(law, setpoint, time)

    def regulate(
        self,
        time: float,
        at: ValueSource | float,
        generator: SetPointGenerator | None = None,
        tuning: ControlLawLike | None = None,
        transfer: Transfer = Transfer.TRACK,
    ) -> RegulateResult:
        bump = self.transfer(time, tuning, transfer, self.expected)
        return RegulateResult(*self.set_reference(at=at, time=time, generator=generator), bump)

    def set_reference(
        self, at: float | ValueSource, time: float, generator: SetPointGenerator | None
    ) -> ApplyResult:
        super().set_reference(at, time, generator)
        return self.apply(time)

    def step(self, time: float, value: float) -> ApplyResult:
        super().step(time, value)
        return self.apply(time)

    def apply(self, time: float) -> ApplyResult:
        self.last_demand = self.demand_at(time)
        self.expected = self.actuator.set_demand(self.last_demand)
        return ApplyResult(expected=self.expected, last_demand=self.last_demand)
