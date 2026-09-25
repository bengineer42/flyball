"""Shared plumbing for the MAX318xx thermocouple/RTD family.

MAX6675, MAX31855, MAX31856, MAX31865: all four are Maxim/Analog Devices SPI,
read-only chips; each gets its own module (`max6675.py`, `max31855.py`,
`max31856.py`, `max31865.py`) since the decode maths and register sets
differ, but two things are genuinely shared:

- signed-field decoding (each device packs a reading into the top bits of a
  byte-aligned two's complement field);
- Maxim's common one-byte-address SPI register protocol used by the
  register-addressed pair (MAX31856, MAX31865): a read clocks the register
  address then as many zero bytes as data wanted, address auto-incrementing;
  a write is the address with D7 set, then the data byte. Confirmed
  identical in both datasheets' "Serial Interface" sections.
"""

from __future__ import annotations

from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Ohm
from flyball.hardware.spi import SpiLink

TEMPERATURE = Quantity("temperature", Celsius)
COLD_JUNCTION = Quantity("cold_junction", Celsius)
RESISTANCE = Quantity("resistance", Ohm)


def sign_extend(raw: int, bits: int) -> int:
    """`raw`, a `bits`-wide two's complement field, as a signed Python int."""
    sign_bit = 1 << (bits - 1)
    return (raw & (sign_bit - 1)) - (raw & sign_bit)


def read_registers(link: SpiLink, start: int, count: int) -> bytes:
    """`count` data bytes from consecutive registers from `start` (auto-increment read).

    A full-duplex transfer clocks back as many bytes as it clocks out; the
    first byte is the address echoed back while it was still being sent,
    not data, so it is dropped.
    """
    reply = link.transfer([start, *([0] * count)])
    return reply[1:]


def write_register(link: SpiLink, address: int, value: int) -> None:
    """One register write: `address` with the write bit (D7) set, then the data byte."""
    link.transfer([address | 0x80, value])


__all__ = [
    "COLD_JUNCTION",
    "RESISTANCE",
    "TEMPERATURE",
    "read_registers",
    "sign_extend",
    "write_register",
]
