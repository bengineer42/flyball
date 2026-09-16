"""Devices shared by the suite. Not a conftest: imported once, by name."""

from __future__ import annotations

from dataclasses import dataclass

from flyball.core.device import Device, DeviceState, command
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, SignalSpec
from flyball.core.units.si import Watt


@dataclass(frozen=True, slots=True, kw_only=True)
class DutyState(DeviceState):
    duty: float = 0.0


class DutyHeater(Device):
    """A heater with one W signal and two commands, for device and route tests."""

    TREE = (
        SignalSpec(
            name="power", quantity=Quantity("power", Watt), access=Access.W, limits=(0.0, 100.0)
        ),
    )

    def __init__(self, name: str, label: str | None = None) -> None:
        super().__init__(name, label)
        self.duty = 0.0

    @property
    def state(self) -> DutyState:
        return DutyState(duty=self.duty)

    @command
    def set_duty(self, duty: float, ramp_s: float = 0.0) -> DutyState:
        """Drive the element at a fixed duty."""
        self.duty = duty
        return self.state

    @command(tag="off")
    def switch_off(self) -> None:
        """Stop heating."""
        self.duty = 0.0
