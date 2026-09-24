"""A driver with an output, a demand and a setting.

`read` polls the output, `commit` writes the demand, and a command's
argument links to the setting it changes.
"""

from __future__ import annotations

from typing import Annotated

from flyball.foundation.device.descriptors import Demand, Setting
from flyball.foundation.device.device import (
    Committable,
    DriverConfig,
    Readout,
    Readable,
    command,
)
from flyball.foundation.quantities.quantity import Quantity
from flyball.foundation.quantities.si import Celsius, Watt

TEMPERATURE = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)


# --8<-- [start:heater-config]
class HeaterConfig(DriverConfig["Heater"], type="heater"):
    """What the heater is built from. Rebuild to change."""

    max_power_w: float = 500.0

    def build(self, name: str, label: str | None = None) -> Heater:
        return Heater(name, self, label)
# --8<-- [end:heater-config]


# --8<-- [start:heater]
class Heater(Readable, Committable):
    """Turns a temperature demand into a power, capped by a settable fraction of the maximum.

    `demand` has no command of its own, so the rig synthesises `set_demand`;
    `limit` is re-set by `set_limit`, whose argument links to it.
    """

    demand = Demand("demand", "Target temperature", TEMPERATURE, limits=(0.0, 300.0))
    power = Readout("power", "Power drawn", POWER)
    limit = Setting("limit", "Power limit", initial=1.0)  # fraction of max_power_w

    # --8<-- [start:heater-init]
    def __init__(self, name: str, config: HeaterConfig, label: str | None = None) -> None:
        super().__init__(name, label)
        self._config = config
        self._power = 0.0

    @property
    def config(self) -> HeaterConfig:
        return self._config
    # --8<-- [end:heater-init]

    # --8<-- [start:heater-read-commit]
    def read(self, time_ns: int, node=None):
        """Poll the power meter."""
        yield self.sample(time_ns, power=self._power)

    def commit(self, time_ns: int) -> None:
        """Push the demand to the element, capped by `limit`."""
        if (target := self.demand.staged) is not None:
            wanted = max(0.0, (target - 20.0) * 5.0)  # 5 W per degree above ambient
            self._power = min(wanted, self._config.max_power_w * self.limit.value)
    # --8<-- [end:heater-read-commit]

    # --8<-- [start:heater-command]
    @command
    def set_limit(self, fraction: Annotated[float, limit]) -> None:
        """Cap the power a controller may use, as a fraction of the maximum."""
        self.limit.push(max(0.0, min(1.0, fraction)))
    # --8<-- [end:heater-command]
# --8<-- [end:heater]
