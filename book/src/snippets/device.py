"""An actuator with all three tiers and a command."""

from dataclasses import dataclass

from flyball.core import Actuator
from flyball.core.device import Condition, Level, command
from flyball.core.sink import ActuatorConfig, ActuatorSettings, ActuatorState
from flyball.core.units.si import Celsius


class HeaterConfig(ActuatorConfig["Heater"], tag="heater"):
    """What the heater is built from. Rebuild to change."""

    name: str = "heater"
    max_power_w: float = 500.0

    def build(self) -> "Heater":
        return Heater(self)


@dataclass(frozen=True, slots=True, kw_only=True)
class HeaterSettings(ActuatorSettings):
    """What a command can change while it runs."""

    limit: float = 1.0  # fraction of max_power_w the loop may use


@dataclass(frozen=True, slots=True, kw_only=True)
class HeaterState(ActuatorState):
    """What it reports now."""

    power_w: float = 0.0


class Heater(Actuator):
    """Turns a temperature demand into a power."""

    demand_unit = Celsius

    def __init__(self, config: HeaterConfig) -> None:
        super().__init__(config.name)
        self._config = config
        self._limit = 1.0
        self._demand: float | None = None
        self._power = 0.0
        self._railed_since: int | None = None

    def set_demand(self, demand: float) -> float | None:
        self._demand = demand
        wanted = max(0.0, (demand - 20.0) * 5.0)  # a crude gain: 5 W per degree above ambient
        ceiling = self._config.max_power_w * self._limit
        self._power = min(wanted, ceiling)
        return None if wanted > ceiling else demand  # railed: cannot say what it will deliver

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
        return HeaterState(demand=self._demand, power_w=self._power, conditions=conditions)

    @command
    def set_limit(self, limit: float) -> HeaterSettings:
        """Cap the power the loop may use, as a fraction of the maximum."""
        self._limit = max(0.0, min(1.0, limit))
        return self.settings

    @command(tag="off")
    def switch_off(self) -> None:
        """Cut the power."""
        self._power = 0.0
