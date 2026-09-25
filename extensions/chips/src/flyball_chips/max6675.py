"""Maxim MAX6675: cold-junction-compensated K-type thermocouple to digital, SPI, read-only.

The 16-bit word (datasheet "Serial Interface" and Fig. 1) is, MSB first:
D15 a dummy sign bit (always 0), D14-D3 the 12-bit temperature in 0.25°C
steps (positive Celsius only -- the MAX6675 has no negative-temperature
mode, unlike the MAX31855), D2 the thermocouple-input-open flag (1 = no
thermocouple attached), D1 a device-ID bit (always low), D0 a three-state
output bit (always low). [Confirmed against the Maxim MAX6675 datasheet's
"Serial Interface" section and Fig. 1; this decode -- shift right 3, mask
to 12 bits, x0.25, bit 2 for open -- is also what essentially every
MAX6675 library (Arduino, CircuitPython, ...) implements.]

An open thermocouple is `invalid("open_circuit")`, never a raise: the chip
answered, it just has nothing attached.
"""

from __future__ import annotations

from collections.abc import Iterator

from flyball.foundation.config import resolve
from flyball.foundation.device import DriverConfig, Node, Readable, Readout, Sample, Value, invalid
from flyball.hardware.spi import SpiLink

from flyball_chips._links import SpiLinkConfig
from flyball_chips._max318xx import TEMPERATURE

OPEN_BIT = 0x0004
"""D2: no thermocouple attached."""


def decode(reply: bytes) -> Value:
    """The two-byte word -> °C, or `invalid("open_circuit")` if D2 (open) is set."""
    word = int.from_bytes(reply[:2], "big")
    if word & OPEN_BIT:
        return invalid("open_circuit")
    return ((word >> 3) & 0x0FFF) * 0.25


class Max6675(Readable):
    """One chip: `temperature [RP]`, one SPI transfer per read."""

    temperature = Readout("temperature", quantity=TEMPERATURE, precision=2)

    def __init__(self, name: str, link: SpiLink, label: str | None = None) -> None:
        super().__init__(name, label)
        self.link = link

    @property
    def config(self) -> Max6675Config:
        return Max6675Config(link="")

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        yield self.sample(time_ns, temperature=decode(self.link.transfer([0, 0])))


class Max6675Config(DriverConfig[Max6675], type="max6675"):
    """`driver: max6675`: a K-type thermocouple over SPI, no other fields."""

    link: SpiLinkConfig | str  # type: ignore[valid-type]

    def build(self, name: str, label: str | None = None) -> Max6675:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Max6675(name, resolve(self.link), label=label)


Max6675.config_type = Max6675Config  # the config is declared after the device it builds


__all__ = ["OPEN_BIT", "Max6675", "Max6675Config", "decode"]
