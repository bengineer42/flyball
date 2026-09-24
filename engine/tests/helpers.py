"""Devices shared by the suite. Not a conftest: imported once, by name."""

from __future__ import annotations

from flyball.foundation.device import Committable, Demand, Readout, command
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Watt


class DutyHeater(Committable):
    """A heater with one demand, a duty output and two commands, for device and route tests."""

    power = Demand("power", "Power", Quantity("power", Watt), limits=(0.0, 100.0))
    duty = Readout("duty", "Duty", initial=0.0)

    @command
    def set_duty(self, duty: float, ramp_s: float = 0.0) -> float:
        """Drive the element at a fixed duty."""
        self.duty.push(duty)
        return duty

    @command(name="off")
    def switch_off(self) -> None:
        """Stop heating."""
        self.duty.push(0.0)
