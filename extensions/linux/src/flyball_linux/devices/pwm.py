"""A PWM channel as a device: one `[RPW]` demand `drive`, committed as the duty.

With no `unit` the signal is the duty itself, 0 to 1 of full. With `unit`
and `span` the signal is in that unit and `span = [d0, d1]` is the value
that means 0 % and the one that means 100 %, mapped linearly: a static
feedforward the controller's correction works around. A heater that holds
40 °C flat out and 10 °C off has `unit: °C, span: [10, 40]`, and its
`drive` signal is in °C with `limits` the span -- the same rule as
`sim_drive`'s spelled-out port.
"""

from __future__ import annotations

from flyball.foundation.config import resolve
from flyball.foundation.device import Bounds, Committable, DriverConfig, Setting, Signal, command
from flyball.foundation.quantities import DIMENSIONLESS, Quantity
from flyball.foundation.quantities.si import Hertz
from flyball.hardware.spanned_demand import (
    from_fraction,
    spanned_signal_spec,
    to_fraction,
    validate_span,
)
from pydantic import Field, model_validator

from flyball_linux.links.pwm import PwmLink, PwmLinkConfig

# The same unit `flyball.sim.devices` gives a plant's drive, defined alike so the two agree.
Drive = DIMENSIONLESS.unit("fraction of full drive", "of full")
DRIVE = Quantity("drive", Drive)
FREQUENCY = Quantity("frequency", Hertz)


class PwmChannel(Committable):
    """Drives a channel's duty from its `drive` demand. A heater, a fan, an LED.

    The channel is enabled at 0 % on construction, so a heater is off from
    the moment the rig has it.
    """

    frequency_hz = Setting("frequency_hz", "Carrier frequency", FREQUENCY)

    def __init__(
        self,
        name: str,
        link: PwmLink,
        channel: int,
        frequency_hz: float = 1000.0,
        invert: bool = False,
        unit: str | None = None,
        quantity: str | None = None,
        span: Bounds | None = None,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        validate_span(unit, span, prefix=f"{name}: ")
        self.link = link
        self.channel = channel
        self.invert = invert
        self.span = span
        self._enabled = False
        self._duty = 0.0
        """The fraction last driven, 0 to 1: independent of `drive`'s reading, to re-apply at a
        new `frequency_hz` even when the commit that set it pushed no readback."""
        self.bind((spanned_signal_spec("drive", unit, quantity, span, bare=DRIVE),))
        self.frequency_hz.push(frequency_hz)
        self._drive(0.0)

    @property
    def config(self) -> PwmChannelConfig:
        signal = self.signals["drive"]
        return PwmChannelConfig(
            link="",
            channel=self.channel,
            frequency_hz=self.frequency_hz.value,
            invert=self.invert,
            unit=None if self.span is None else signal.unit.symbol,
            quantity=None if self.span is None else signal.quantity.name,
            span=self.span,
        )

    def fraction(self, value: float) -> float:
        """The duty a value of `drive` asks for: itself, or linear over `span`."""
        return to_fraction(value, self.span)

    def _drive(self, duty: float) -> float:
        """Drive the channel at `duty` (0 to 1, clamped); returns the duty actually achieved."""
        self._duty = duty = min(1.0, max(0.0, duty))
        period_ns = round(1e9 / self.frequency_hz.value)
        physical = 1.0 - duty if self.invert else duty
        duty_ns = round(period_ns * physical)
        self.link.configure(self.channel, period_ns, duty_ns)
        if not self._enabled:
            self.link.enable(self.channel, True)
            self._enabled = True
        achieved = duty_ns / period_ns
        return 1.0 - achieved if self.invert else achieved

    def write_signal(self, signal: Signal, value: float) -> None:
        achieved = from_fraction(self._drive(self.fraction(value)), self.span)
        if achieved != value:
            signal.push(achieved)

    @command
    def set_frequency(self, frequency_hz: float) -> None:
        """Change the carrier; the duty is re-applied at the new period."""
        if frequency_hz <= 0:
            raise ValueError("frequency must be positive")
        self.frequency_hz.push(frequency_hz)
        self._drive(self._duty)

    @command(writes=("drive",))
    def off(self) -> None:
        """Duty to zero and the channel disabled, until the next demand.

        Refused while a controller drives `drive`: put it in manual first.
        """
        self._drive(0.0)
        self.link.enable(self.channel, False)
        self._enabled = False
        self.signals["drive"].push(0.0 if self.span is None else self.span[0])


class PwmChannelConfig(DriverConfig[PwmChannel], type="pwm_channel"):
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
    span: Bounds | None = Field(
        default=None, description="With `unit`: the value meaning 0 % and the one meaning 100 %."
    )

    @model_validator(mode="after")
    def _unit_with_span(self) -> PwmChannelConfig:
        validate_span(self.unit, self.span)
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


__all__ = ["DRIVE", "FREQUENCY", "Drive", "PwmChannel", "PwmChannelConfig"]
