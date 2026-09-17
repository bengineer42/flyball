"""Sensirion SHT30/31/35: temperature and humidity over I2C.

Command byte, a wait, six bytes back: two of temperature, a CRC, two of
humidity, a CRC (temperature first, unlike a register-mapped chip -- this is
not a table). Decoded from Sensirion's public "Datasheet SHT3x-DIS" (May
2015, Version 0.93): section 4.3 for the measurement commands, 4.12 for the
CRC properties, 4.13 for the conversion formulas.

[Unverified] This module has never been run against real SHT3x hardware --
only against the scripted `FakeI2c` in `tests/test_sht31.py`.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Literal

from flyball.core.config import resolve
from flyball.core.device import DriverConfig, Output, Readable
from flyball.core.quantity import Quantity
from flyball.core.signal import Node, Sample
from flyball.core.units.dimensions import Fraction
from flyball.core.units.si import Celsius
from pydantic import Field

from flyball_linux.devices.chips._sensirion import crc8, crc_words
from flyball_linux.links.i2c import I2cLink, I2cLinkConfig

Precision = Literal["high", "medium", "low"]
COMMANDS: dict[str, tuple[int, float]] = {
    "high": (0x2400, 0.015),
    "medium": (0x240B, 0.006),
    "low": (0x2416, 0.004),
}
"""Single-shot measurement command (clock stretching disabled) and the
datasheet's maximum conversion time, by repeatability (section 4.3, Table 8)."""

PercentRH = Fraction.unit("percent relative humidity", "%RH", 0.01, scale=(0.0, 100.0))
HUMIDITY = Quantity("humidity", PercentRH)
TEMPERATURE = Quantity("temperature", Celsius)
SHT31_ADDRESS = 0x44
"""ADDR pin tied low; ADDR tied high answers at 0x45 (Table 7)."""


def decode(frame: bytes) -> tuple[float, float]:
    """(°C, %RH) from the six-byte reply: temperature word first, then humidity.

    Raises:
        HardwareError: A CRC that does not match.
    """
    raw_t, raw_h = crc_words(frame, 2)
    temperature = -45.0 + 175.0 * raw_t / 65535.0
    humidity = min(100.0, max(0.0, 100.0 * raw_h / 65535.0))
    return temperature, humidity


def encode(temperature: float, humidity: float) -> bytes:
    """The frame the chip would send for these values; for fakes and tests."""
    raw_t = round((temperature + 45.0) / 175.0 * 65535.0)
    raw_h = round(humidity / 100.0 * 65535.0)
    t = raw_t.to_bytes(2, "big")
    h = raw_h.to_bytes(2, "big")
    return t + bytes([crc8(t)]) + h + bytes([crc8(h)])


class Sht31Sensor:
    """One chip at `address`: command, wait, read, decode, in one `read`."""

    __slots__ = ("address", "link", "precision", "sleep")

    def __init__(
        self, link: I2cLink, address: int, precision: Precision = "high", sleep: bool = True
    ) -> None:
        self.link = link
        self.address = address
        self.precision: Precision = precision
        self.sleep = sleep
        """Whether to wait the conversion time; off in a test against a fake."""

    def read(self) -> tuple[float, float]:
        """(°C, %RH): one I2C transaction."""
        command, wait_s = COMMANDS[self.precision]
        self.link.write(self.address, [command >> 8, command & 0xFF])
        if self.sleep:
            time.sleep(wait_s)
        return decode(self.link.read(self.address, 6))


class Sht31(Readable):
    """One chip on the device root: `humidity`, `temperature [RP]`, one I2C transaction."""

    humidity = Output("humidity", quantity=HUMIDITY, range=(0.0, 100.0), precision=2)
    temperature = Output("temperature", quantity=TEMPERATURE, range=(-40.0, 125.0), precision=2)

    def __init__(
        self,
        name: str,
        link: I2cLink,
        address: int = SHT31_ADDRESS,
        precision: Precision = "high",
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.sensor = Sht31Sensor(link, address, precision, sleep)

    @property
    def config(self) -> Sht31Config:
        return Sht31Config(link="", address=self.sensor.address, precision=self.sensor.precision)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        temperature, humidity = self.sensor.read()
        yield self.sample(time_ns, humidity=humidity, temperature=temperature)


class Sht31Config(DriverConfig[Sht31], tag="sht31"):
    """One chip by its I2C address."""

    link: I2cLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(default=SHT31_ADDRESS, ge=0x03, le=0x77)
    precision: Precision = "high"

    def build(self, name: str, label: str | None = None) -> Sht31:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Sht31(name, resolve(self.link), self.address, self.precision, label=label)


Sht31.config_type = Sht31Config  # the config is declared after the device it builds


__all__ = [
    "COMMANDS",
    "HUMIDITY",
    "SHT31_ADDRESS",
    "TEMPERATURE",
    "PercentRH",
    "Precision",
    "Sht31",
    "Sht31Config",
    "Sht31Sensor",
    "crc8",
    "decode",
    "encode",
]
