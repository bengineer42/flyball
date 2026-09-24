"""Maxim MAX31855: cold-junction-compensated thermocouple to digital, SPI, read-only.

One 32-bit word per read (datasheet "Register Map", Table 1), MSB first:

| bits | field |
| --- | --- |
| D31-D18 | thermocouple temperature, signed, 0.25°C/LSB (14-bit) |
| D17 | reserved |
| D16 | FAULT: set if any of D2-D0 is set |
| D15-D4 | internal (cold-junction) temperature, signed, 0.0625°C/LSB (12-bit) |
| D3 | reserved |
| D2 | SCV: thermocouple shorted to VCC |
| D1 | SCG: thermocouple shorted to GND |
| D0 | OC: open circuit (no thermocouple attached) |

[Confirmed against the Maxim MAX31855 datasheet's "Register Map"; this bit
layout is also what the Adafruit MAX31855 library decodes.]

A fault (D16 set) makes `temperature` `invalid(reason)`, `reason` naming
every one of D2-D0 that is set (joined with `+`; more than one can be set
at once, e.g. a floating input can read as both open and shorted). The
internal cold-junction reading has no fault bit of its own -- it is always
published, fault or not.
"""

from __future__ import annotations

from collections.abc import Iterator

from flyball.foundation.config import resolve
from flyball.foundation.device import DriverConfig, Node, Readable, Readout, Sample, Value, invalid
from flyball.hardware.spi import SpiLink

from flyball_chips._links import SpiLinkConfig
from flyball_chips._max318xx import COLD_JUNCTION, TEMPERATURE, sign_extend

FAULT_BIT = 0x00010000
SCV_BIT = 0x00000004
"""Thermocouple shorted to VCC."""
SCG_BIT = 0x00000002
"""Thermocouple shorted to GND."""
OC_BIT = 0x00000001
"""Open circuit: no thermocouple attached."""


def _fault_reason(word: int) -> str:
    reasons = []
    if word & OC_BIT:
        reasons.append("open_circuit")
    if word & SCG_BIT:
        reasons.append("short_to_gnd")
    if word & SCV_BIT:
        reasons.append("short_to_vcc")
    return "+".join(reasons) or "fault"


def decode(reply: bytes) -> tuple[Value, float]:
    """(thermocouple °C, or `invalid`; cold-junction °C) from the four-byte word."""
    word = int.from_bytes(reply[:4], "big")
    cold_junction = sign_extend((word >> 4) & 0x0FFF, 12) * 0.0625
    if word & FAULT_BIT:
        return invalid(_fault_reason(word)), cold_junction
    thermocouple = sign_extend((word >> 18) & 0x3FFF, 14) * 0.25
    return thermocouple, cold_junction


class Max31855(Readable):
    """One chip: `temperature [RP]` (thermocouple) and `cold_junction [RP]`."""

    temperature = Readout("temperature", quantity=TEMPERATURE, precision=2)
    cold_junction = Readout("cold_junction", quantity=COLD_JUNCTION, precision=4)

    def __init__(self, name: str, link: SpiLink, label: str | None = None) -> None:
        super().__init__(name, label)
        self.link = link

    @property
    def config(self) -> Max31855Config:
        return Max31855Config(link="")

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        temperature, cold_junction = decode(self.link.transfer([0, 0, 0, 0]))
        yield self.sample(time_ns, temperature=temperature, cold_junction=cold_junction)


class Max31855Config(DriverConfig[Max31855], type="max31855"):
    """`driver: max31855`: a thermocouple with cold-junction compensation over SPI."""

    link: SpiLinkConfig | str  # type: ignore[valid-type]

    def build(self, name: str, label: str | None = None) -> Max31855:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Max31855(name, resolve(self.link), label=label)


Max31855.config_type = Max31855Config  # the config is declared after the device it builds


__all__ = [
    "FAULT_BIT",
    "OC_BIT",
    "SCG_BIT",
    "SCV_BIT",
    "Max31855",
    "Max31855Config",
    "decode",
]
