"""Microchip MCP4725: single-channel, 12-bit buffered I2C DAC.

[Unverified against real hardware.] This driver has never been run against a
physical MCP4725; its protocol is decoded solely from Microchip's public
datasheet (DS22039D, "MCP4725 12-Bit Digital-to-Analog Converter with EEPROM
Memory"), Figure 6-1 ("Fast Mode Write Command") and Table 5-2 ("Power-Down
Bits").

The chip has no register-address byte -- unlike the chips `i2c_table`
targets, a plain write is ambiguous, so it needs its own driver. Only the
"Fast Mode" write is implemented: two bytes, no command byte, sent after the
address byte on every write. Byte 1 is `00 PD1 PD0 D11 D10 D9 D8`, byte 2 is
`D7 D6 D5 D4 D3 D2 D1 D0` -- `PD1:PD0` selects the power-down output
impedance (`00` normal, the chip's output op-amp driven; the others tie
`VOUT` to ground through 1k/100k/500k). A demand writes normal mode (`PD = 00`);
`power_down` writes `PD = 01` (1 kΩ to ground) and is the device's stop -- 0 V is a
setpoint for a positioner or a VFD, so the driver declares no `off`, but the chip's
own power-down is its inactive state. The next demand powers it up again. The three-byte
"Write DAC Register" command (which also programs the EEPROM default) is not
implemented.

This is the write-side mirror of an analog input like `mcp3008`: it drives a
0-1 fraction of full scale (12-bit, `VOUT = VDD * code / 4096`) into an
external op-amp stage a rig uses for a 0-10 V (or similar) control signal --
a VFD speed reference, a dimmable ballast, a damper actuator. With `unit`
and `span` (as `pwm_channel` has) the signal is commanded directly in
engineering units instead of a bare fraction.
"""

from __future__ import annotations

from flyball.foundation.config import resolve
from flyball.foundation.device import Bounds, Committable, DriverConfig, Signal, command
from flyball.foundation.quantities import DIMENSIONLESS, Quantity
from flyball.hardware.i2c import I2cLink
from flyball.hardware.spanned_demand import (
    from_fraction,
    spanned_signal_spec,
    to_fraction,
    validate_span,
)
from pydantic import Field, model_validator

from flyball_chips._links import I2cLinkConfig

MCP4725_ADDRESS = 0x60
"""Device code `1100` with A2=A1=A0=0 (A0 tied to VSS); the -A0T variant answers at 0x61."""

FULL_SCALE = 4095
"""The top 12-bit code (`VOUT` = `VDD` at this code)."""

Fraction = DIMENSIONLESS.unit("fraction of full scale", "of full")
DRIVE = Quantity("drive", Fraction)


def encode(code: int, power_down: int = 0) -> bytes:
    """The two Fast Mode write bytes for a 12-bit `code` and power-down selection.

    Byte 1: `0 0 PD1 PD0 D11 D10 D9 D8`; byte 2: `D7 D6 D5 D4 D3 D2 D1 D0`
    (datasheet Figure 6-1).
    """
    if not 0 <= code <= FULL_SCALE:
        raise ValueError(f"code {code} out of range 0..{FULL_SCALE}")
    if not 0 <= power_down <= 3:
        raise ValueError(f"power_down {power_down} out of range 0..3")
    byte1 = ((power_down & 0x3) << 4) | ((code >> 8) & 0x0F)
    byte2 = code & 0xFF
    return bytes([byte1, byte2])


class Mcp4725Output:
    """One chip at `i2c_address`: a fraction 0-1, written as a Fast Mode command."""

    __slots__ = ("i2c_address", "link")

    def __init__(self, link: I2cLink, i2c_address: int = MCP4725_ADDRESS) -> None:
        self.link = link
        self.i2c_address = i2c_address

    def write(self, fraction: float) -> float:
        """Write `fraction` (0 to 1, clamped) of full scale; returns the fraction achieved."""
        fraction = min(1.0, max(0.0, fraction))
        code = round(fraction * FULL_SCALE)
        self.link.write(self.i2c_address, encode(code))
        return code / FULL_SCALE

    def power_down(self) -> None:
        """Power the output down: `VOUT` to ground through 1 kΩ (`PD = 01`), code 0."""
        self.link.write(self.i2c_address, encode(0, power_down=1))


class Mcp4725(Committable):
    """Drives `drive` (0-1 of full scale, or `unit`/`span` mapped) out the DAC on every write."""

    def __init__(
        self,
        name: str,
        link: I2cLink,
        i2c_address: int = MCP4725_ADDRESS,
        unit: str | None = None,
        quantity: str | None = None,
        span: Bounds | None = None,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        validate_span(unit, span, prefix=f"{name}: ")
        self.output = Mcp4725Output(link, i2c_address)
        self.span = span
        self.bind((spanned_signal_spec("drive", unit, quantity, span, bare=DRIVE),))
        self._write(0.0)

    @property
    def config(self) -> Mcp4725Config:
        signal = self.signals["drive"]
        return Mcp4725Config(
            link="",
            i2c_address=self.output.i2c_address,
            unit=None if self.span is None else signal.unit.symbol,
            quantity=None if self.span is None else signal.quantity.name,
            span=self.span,
        )

    def fraction(self, value: float) -> float:
        """The 0-1 fraction a value of `drive` asks for: itself, or linear over `span`."""
        return to_fraction(value, self.span)

    def _write(self, fraction: float) -> float:
        return self.output.write(fraction)

    def write_signal(self, signal: Signal, value: float) -> None:
        achieved = from_fraction(self._write(self.fraction(value)), self.span)
        if achieved != value:
            signal.push(achieved)

    @command(stops=True, writes=("drive",))
    def power_down(self) -> None:
        """Power the output down (1 kΩ to ground); the next demand powers it up.

        The device's stop: a rig stop runs it.
        """
        self.output.power_down()
        self.signals["drive"].push(from_fraction(0.0, self.span))


class Mcp4725Config(DriverConfig[Mcp4725], type="mcp4725"):
    """`driver: mcp4725`: `{ link, i2c_address }`, in a bare 0-1 fraction unless `unit` and `span`."""

    link: I2cLinkConfig | str  # type: ignore[valid-type]
    i2c_address: int = Field(default=MCP4725_ADDRESS, ge=0x03, le=0x77)
    unit: str | None = Field(
        default=None, description="The unit `drive` is set in; omitted, it is the fraction itself."
    )
    quantity: str | None = Field(
        default=None, description="With `unit`: what `drive` then is ('drive')."
    )
    span: Bounds | None = Field(
        default=None, description="With `unit`: the value meaning 0 % and the one meaning 100 %."
    )

    @model_validator(mode="after")
    def _unit_with_span(self) -> Mcp4725Config:
        validate_span(self.unit, self.span)
        return self

    def build(self, name: str, label: str | None = None) -> Mcp4725:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Mcp4725(
            name, resolve(self.link), self.i2c_address, self.unit, self.quantity, self.span, label=label
        )


Mcp4725.config_type = Mcp4725Config  # the config is declared after the device it builds


__all__ = [
    "DRIVE",
    "FULL_SCALE",
    "MCP4725_ADDRESS",
    "Fraction",
    "Mcp4725",
    "Mcp4725Config",
    "Mcp4725Output",
    "encode",
]
