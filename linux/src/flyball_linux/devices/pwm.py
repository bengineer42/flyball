"""A PWM channel as an actuator: the demand is a duty fraction."""

from __future__ import annotations

from dataclasses import dataclass

from flyball.core.config import resolve
from flyball.core.device import DeviceConfig, DeviceSettings, command
from flyball.core.sink import Actuator, ActuatorState
from flyball.core.units.dimension import Unit
from pydantic import Field

from flyball_linux.links.pwm import PwmLink, PwmLinkConfig


@dataclass(frozen=True, slots=True, kw_only=True)
class PwmSettings(DeviceSettings):
    frequency_hz: float = 1000.0
    limits: tuple[float, float] = (0.0, 1.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class PwmState(ActuatorState):
    duty: float = 0.0
    """The fraction actually driven: the demand clamped to the limits."""
    enabled: bool = False


class PwmActuator(Actuator):
    """Drives a channel's duty from the demand, clamped to `limits`. A heater, a fan, an LED.

    With no `span` the demand *is* the duty, 0 to 1. With `unit` and `span`
    the demand is in the loop's unit and `span = (d0, d1)` is the demand
    that means 0 % and the one that means 100 %: a static feedforward the
    loop's correction works around. A heater that holds 40 °C flat out and
    10 °C off has `unit = "°C", span = (10, 40)`.
    """

    def __init__(
        self,
        name: str,
        link: PwmLink,
        channel: int,
        frequency_hz: float = 1000.0,
        limits: tuple[float, float] = (0.0, 1.0),
        invert: bool = False,
        unit: str = "1",
        span: tuple[float, float] | None = None,
    ) -> None:
        super().__init__(name)
        self.link = link
        self.channel = channel
        self.frequency_hz = frequency_hz
        self.limits = limits
        self.invert = invert
        self.span = span
        self._demand: float | None = None
        self._duty = 0.0
        self._enabled = False
        self.demand_unit = Unit.get(unit)  # type: ignore[misc]
        self._drive(0.0)

    def feedforward(self, demand: float) -> float:
        """The duty a demand asks for before clamping."""
        if self.span is None:
            return demand
        d0, d1 = self.span
        return (demand - d0) / (d1 - d0)

    @property
    def settings(self) -> PwmSettings:
        return PwmSettings(frequency_hz=self.frequency_hz, limits=self.limits)

    @property
    def state(self) -> PwmState:
        return PwmState(demand=self._demand, duty=self._duty, enabled=self._enabled)

    def _drive(self, duty: float) -> None:
        low, high = self.limits
        self._duty = min(high, max(low, duty))
        period_ns = round(1e9 / self.frequency_hz)
        fraction = 1.0 - self._duty if self.invert else self._duty
        self.link.configure(self.channel, period_ns, round(period_ns * fraction))
        if not self._enabled:
            self.link.enable(self.channel, True)
            self._enabled = True

    def set_demand(self, demand: float) -> float | None:
        self._demand = demand
        self._drive(self.feedforward(demand))
        return None  # what the process does with the duty is the reader's to report

    @command
    def set_frequency(self, frequency_hz: float) -> PwmSettings:
        """Change the carrier; the duty is re-applied at the new period."""
        if frequency_hz <= 0:
            raise ValueError("frequency must be positive")
        self.frequency_hz = frequency_hz
        self._drive(self._duty)
        return self.settings

    @command
    def set_limits(self, low: float, high: float) -> PwmSettings:
        """Bound the duty: a heater that must never exceed 60 %."""
        if not 0.0 <= low < high <= 1.0:
            raise ValueError("limits must satisfy 0 <= low < high <= 1")
        self.limits = (low, high)
        self._drive(self._duty)
        return self.settings

    @command
    def off(self) -> PwmState:
        """Duty to zero and the channel disabled, until the next demand."""
        self._drive(0.0)
        self.link.enable(self.channel, False)
        self._enabled = False
        return self.state


class PwmActuatorConfig(DeviceConfig[PwmActuator], tag="pwm_actuator"):
    name: str
    link: PwmLinkConfig | str  # type: ignore[valid-type]
    channel: int = Field(ge=0)
    frequency_hz: float = Field(default=1000.0, gt=0)
    limits: tuple[float, float] = (0.0, 1.0)
    invert: bool = False
    unit: str = Field(default="1", description="The loop's demand unit; `1` means the duty itself.")
    span: tuple[float, float] | None = Field(
        default=None, description="The demand meaning 0 % and the one meaning 100 %."
    )

    def build(self) -> PwmActuator:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a chip before building")
        return PwmActuator(
            self.name,
            resolve(self.link),
            self.channel,
            self.frequency_hz,
            self.limits,
            self.invert,
            self.unit,
            self.span,
        )


__all__ = ["PwmActuator", "PwmActuatorConfig", "PwmSettings", "PwmState"]
