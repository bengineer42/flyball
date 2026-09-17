"""A driver with an output, a demand and a setting.

`read` polls the output, `commit` writes the demand, and a command's
argument links to the setting it changes.
"""

from __future__ import annotations

from typing import Annotated

from flyball.core.device import (
    Committable,
    Demand,
    DriverConfig,
    Output,
    Readable,
    Setting,
    command,
)
from flyball.core.quantity import Quantity
from flyball.core.units.si import Celsius, Watt

TEMPERATURE = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)


class HeaterConfig(DriverConfig["Heater"], tag="heater"):
    """What the heater is built from. Rebuild to change."""

    max_power_w: float = 500.0

    def build(self, name: str, label: str | None = None) -> Heater:
        return Heater(name, self, label)


class Heater(Readable, Committable):
    """Turns a temperature demand into a power, capped by a settable fraction of the maximum.

    `demand` has no command of its own, so the rig synthesises `set_demand`;
    `limit` is re-set by `set_limit`, whose argument links to it.
    """

    demand = Demand("demand", "Target temperature", TEMPERATURE, limits=(0.0, 300.0))
    power = Output("power", "Power drawn", POWER)
    limit = Setting("limit", "Power limit", initial=1.0)  # fraction of max_power_w

    def __init__(self, name: str, config: HeaterConfig, label: str | None = None) -> None:
        super().__init__(name, label)
        self._config = config
        self._power = 0.0

    @property
    def config(self) -> HeaterConfig:
        return self._config

    def read(self, time_ns: int, node=None):
        """Poll the power meter."""
        yield self.sample(time_ns, power=self._power)

    def commit(self, time_ns: int) -> None:
        """Push the demand to the element, capped by `limit`."""
        if (target := self.demand.pending) is not None:
            wanted = max(0.0, (target - 20.0) * 5.0)  # 5 W per degree above ambient
            self._power = min(wanted, self._config.max_power_w * self.limit.value)

    @command
    def set_limit(self, fraction: Annotated[float, limit]) -> None:
        """Cap the power a controller may use, as a fraction of the maximum."""
        self.limit.push(max(0.0, min(1.0, fraction)))
