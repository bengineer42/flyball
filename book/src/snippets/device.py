"""A device with all three tiers, a command, and a condition."""

from dataclasses import dataclass

from flyball.core import Access, Quantity, SignalSpec
from flyball.core.device import (
    Condition,
    Device,
    DeviceSettings,
    DeviceState,
    DriverConfig,
    Level,
    command,
)
from flyball.core.units.si import Celsius

TEMPERATURE = Quantity("temperature", Celsius)


class HeaterConfig(DriverConfig["Heater"], tag="heater"):
    """What the heater is built from. Rebuild to change."""

    max_power_w: float = 500.0

    def build(self, name: str, label: str | None = None) -> "Heater":
        return Heater(name, self, label)


@dataclass(frozen=True, slots=True, kw_only=True)
class HeaterSettings(DeviceSettings):
    """What a command can change while it runs."""

    limit: float = 1.0  # fraction of max_power_w a controller may use


@dataclass(frozen=True, slots=True, kw_only=True)
class HeaterState(DeviceState):
    """What it reports now."""

    power_w: float = 0.0


class Heater(Device):
    """Turns a temperature demand into a power. One writable signal."""

    TREE = (SignalSpec(name="demand", quantity=TEMPERATURE, access=Access.W),)

    def __init__(self, name: str, config: HeaterConfig, label: str | None = None) -> None:
        super().__init__(name, label)
        self._config = config
        self._limit = 1.0
        self._power = 0.0

    def write_signal(self, signal, value: float) -> None:
        wanted = max(0.0, (value - 20.0) * 5.0)  # a crude gain: 5 W per degree above ambient
        self._power = min(wanted, self._config.max_power_w * self._limit)

    @property
    def config(self) -> HeaterConfig:
        return self._config

    @property
    def settings(self) -> HeaterSettings:
        return HeaterSettings(limit=self._limit)

    @property
    def state(self) -> HeaterState:
        conditions = ()
        if self._power >= self._config.max_power_w * self._limit:
            conditions = (Condition("railed", Level.WARNING, "at the power limit", 0),)
        return HeaterState(power_w=self._power, conditions=conditions)

    @command
    def set_limit(self, limit: float) -> HeaterSettings:
        """Cap the power a controller may use, as a fraction of the maximum."""
        self._limit = max(0.0, min(1.0, limit))
        return self.settings

    @command(tag="off")
    def switch_off(self) -> None:
        """Cut the power."""
        self._power = 0.0
