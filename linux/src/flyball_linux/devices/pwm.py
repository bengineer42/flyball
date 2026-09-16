"""A PWM channel as a device: one `[W]` signal `drive`, committed as the duty.

With no `unit` the signal is the duty itself, 0 to 1 of full. With `unit`
and `span` the signal is in that unit and `span = [d0, d1]` is the value
that means 0 % and the one that means 100 %, mapped linearly: a static
feedforward the controller's correction works around. A heater that holds
40 °C flat out and 10 °C off has `unit: °C, span: [10, 40]`, and its
`drive` signal is in °C with `limits` the span -- the same rule as
`sim_drive`'s spelled-out port.
"""

from __future__ import annotations

from dataclasses import dataclass

from flyball.core.config import resolve
from flyball.core.device import Device, DeviceSettings, DeviceState, DriverConfig, command
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, Band, Signal, SignalSpec
from flyball.core.units import DIMENSIONLESS
from pydantic import Field, model_validator

from flyball_linux.links.pwm import PwmLink, PwmLinkConfig

# The same unit `flyball.sim.devices` gives a plant's drive, defined alike so the two agree.
Drive = DIMENSIONLESS.unit("fraction of full drive", "of full")
DRIVE = Quantity("drive", Drive)


@dataclass(frozen=True, slots=True, kw_only=True)
class PwmSettings(DeviceSettings):
    frequency_hz: float = 1000.0


@dataclass(frozen=True, slots=True, kw_only=True)
class PwmState(DeviceState):
    duty: float = 0.0
    """The fraction actually driven, 0 to 1."""
    enabled: bool = False


class PwmChannel(Device):
    """Drives a channel's duty from its `drive` signal. A heater, a fan, an LED.

    The channel is enabled at 0 % on construction, so a heater is off from
    the moment the rig has it.
    """

    def __init__(
        self,
        name: str,
        link: PwmLink,
        channel: int,
        frequency_hz: float = 1000.0,
        invert: bool = False,
        unit: str | None = None,
        quantity: str | None = None,
        span: Band | None = None,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        if (unit is None) != (span is None):
            raise ValueError(f"{name}: `unit` and `span` go together")
        if span is not None and span[1] <= span[0]:
            raise ValueError(f"{name}: span must be a rising pair, not {list(span)}")
        self.link = link
        self.channel = channel
        self.frequency_hz = frequency_hz
        self.invert = invert
        self.span = span
        self._duty = 0.0
        self._enabled = False
        if unit is None:
            spec = SignalSpec(name="drive", quantity=DRIVE, access=Access.W, limits=(0.0, 1.0))
        else:
            spec = SignalSpec(
                name="drive",
                quantity=Quantity(quantity or "drive", unit),
                access=Access.W,
                limits=span,
            )
        self.bind((spec,))
        self._drive(0.0)

    @property
    def config(self) -> PwmChannelConfig:
        signal = self.signals["drive"]
        return PwmChannelConfig(
            link="",
            channel=self.channel,
            frequency_hz=self.frequency_hz,
            invert=self.invert,
            unit=None if self.span is None else signal.unit.symbol,
            quantity=None if self.span is None else signal.quantity.name,
            span=self.span,
        )

    @property
    def settings(self) -> PwmSettings:
        return PwmSettings(frequency_hz=self.frequency_hz)

    @property
    def state(self) -> PwmState:
        return PwmState(duty=self._duty, enabled=self._enabled)

    def fraction(self, value: float) -> float:
        """The duty a value of `drive` asks for: itself, or linear over `span`."""
        if self.span is None:
            return value
        d0, d1 = self.span
        return (value - d0) / (d1 - d0)

    def _drive(self, duty: float) -> None:
        self._duty = min(1.0, max(0.0, duty))
        period_ns = round(1e9 / self.frequency_hz)
        fraction = 1.0 - self._duty if self.invert else self._duty
        self.link.configure(self.channel, period_ns, round(period_ns * fraction))
        if not self._enabled:
            self.link.enable(self.channel, True)
            self._enabled = True

    def write_signal(self, signal: Signal, value: float) -> None:
        self._drive(self.fraction(value))

    @command
    def set_frequency(self, frequency_hz: float) -> PwmSettings:
        """Change the carrier; the duty is re-applied at the new period."""
        if frequency_hz <= 0:
            raise ValueError("frequency must be positive")
        self.frequency_hz = frequency_hz
        self._drive(self._duty)
        return self.settings

    @command
    def off(self) -> PwmState:
        """Duty to zero and the channel disabled, until the next demand."""
        self._drive(0.0)
        self.link.enable(self.channel, False)
        self._enabled = False
        return self.state


class PwmChannelConfig(DriverConfig[PwmChannel], tag="pwm_channel"):
    """`driver: pwm_channel`: `{ link, channel }`, or `pin: PWM0` from the board profile."""

    link: PwmLinkConfig | str  # type: ignore[valid-type]
    channel: int = Field(ge=0)
    frequency_hz: float = Field(default=1000.0, gt=0)
    invert: bool = False
    unit: str | None = Field(
        default=None, description="The unit `drive` is set in; omitted, it is the duty itself."
    )
    quantity: str | None = Field(
        default=None, description="With `unit`: what `drive` then is ('temperature')."
    )
    span: Band | None = Field(
        default=None, description="With `unit`: the value meaning 0 % and the one meaning 100 %."
    )

    @model_validator(mode="after")
    def _unit_with_span(self) -> PwmChannelConfig:
        if (self.unit is None) != (self.span is None):
            raise ValueError("`unit` and `span` go together")
        if self.span is not None and self.span[1] <= self.span[0]:
            raise ValueError("span must be a rising pair")
        return self

    def build(self, name: str, label: str | None = None) -> PwmChannel:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a chip before building")
        return PwmChannel(
            name,
            resolve(self.link),
            self.channel,
            self.frequency_hz,
            self.invert,
            self.unit,
            self.quantity,
            self.span,
            label=label,
        )


PwmChannel.config_type = PwmChannelConfig  # the config is declared after the device it builds


__all__ = ["DRIVE", "Drive", "PwmChannel", "PwmChannelConfig", "PwmSettings", "PwmState"]
