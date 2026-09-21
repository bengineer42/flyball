"""Sensirion SCD30: CO2, temperature and humidity over I2C, continuous-measurement mode.

Command words are 16-bit, some with a 16-bit argument (its own CRC appended).
A measurement reply is three IEEE-754 floats -- CO2 (ppm), temperature (°C),
humidity (%RH) -- each as two CRC-8-protected 16-bit words, 18 bytes total.
The SCD30 also has a Modbus-over-UART mode; this driver is I2C only.

[Unverified] against real hardware: decoded from Sensirion's public
"Interface Description Sensirion SCD30 Sensor Module" PDF and the
`Sensirion/embedded-scd` reference source, never run against a chip.
"""

from __future__ import annotations

import struct
import time
from collections.abc import Iterator

from flyball.foundation.config import resolve
from flyball.foundation.device import DriverConfig, Node, Output, Readable, Sample
from flyball.foundation.errors import HardwareError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, PartsPerMillion
from flyball.hardware.i2c import I2cLink
from pydantic import Field

from flyball_chips._links import I2cLinkConfig
from flyball_chips._sensirion import crc8, crc_words
from flyball_chips.sht4x import PercentRH

CMD_START_CONTINUOUS_MEASUREMENT = 0x0010
CMD_STOP_CONTINUOUS_MEASUREMENT = 0x0104
CMD_SET_MEASUREMENT_INTERVAL = 0x4600
CMD_GET_DATA_READY = 0x0202
CMD_READ_MEASUREMENT = 0x0300
"""Command words, from the interface description. Others (ASC, FRC, altitude,
temperature offset) exist on the chip but are out of scope for this driver."""

SCD30_ADDRESS = 0x61

CO2 = Quantity("co2", PartsPerMillion)
TEMPERATURE = Quantity("temperature", Celsius)
HUMIDITY = Quantity("humidity", PercentRH)


def command(word: int, argument: int | None = None) -> list[int]:
    """The bytes for a command, with its optional 16-bit argument and CRC."""
    data = [word >> 8, word & 0xFF]
    if argument is not None:
        arg = [argument >> 8, argument & 0xFF]
        data += [*arg, crc8(bytes(arg))]
    return data


def decode(frame: bytes) -> tuple[float, float, float]:
    """(CO2 ppm, °C, %RH) from the 18-byte reply: three IEEE-754 floats, two words each.

    Raises:
        HardwareError: A CRC that does not match.
    """
    words = crc_words(frame, 6)
    values = []
    for i in (0, 2, 4):
        raw = bytes([words[i] >> 8, words[i] & 0xFF, words[i + 1] >> 8, words[i + 1] & 0xFF])
        (value,) = struct.unpack(">f", raw)
        values.append(float(value))
    co2, temperature, humidity = values
    return co2, temperature, min(100.0, max(0.0, humidity))


def encode(co2: float, temperature: float, humidity: float) -> bytes:
    """The frame the chip would send for these values; for fakes and tests."""
    out = bytearray()
    for value in (co2, temperature, humidity):
        raw = struct.pack(">f", value)
        for i in (0, 2):
            word = raw[i : i + 2]
            out += word + bytes([crc8(word)])
    return bytes(out)


class Scd30Sensor:
    """One chip at `address`: starts continuous measurement, then polls and reads it."""

    __slots__ = ("address", "link", "sleep", "timeout_s")

    def __init__(
        self,
        link: I2cLink,
        address: int = SCD30_ADDRESS,
        pressure_mbar: int = 0,
        sleep: bool = True,
        timeout_s: float = 3.0,
    ) -> None:
        self.link = link
        self.address = address
        self.sleep = sleep
        """Whether to poll for data-ready and wait between polls; off against a fake."""
        self.timeout_s = timeout_s
        self.link.write(address, command(CMD_START_CONTINUOUS_MEASUREMENT, pressure_mbar))

    def _ready(self) -> bool:
        self.link.write(self.address, command(CMD_GET_DATA_READY))
        (status,) = crc_words(self.link.read(self.address, 3), 1)
        return status == 1

    def read(self) -> tuple[float, float, float]:
        """(CO2 ppm, °C, %RH): waits for data-ready, then one I2C transaction."""
        if self.sleep:
            deadline = time.monotonic() + self.timeout_s
            while not self._ready():
                if time.monotonic() > deadline:
                    raise HardwareError(f"SCD30 measurement not ready within {self.timeout_s}s")
                time.sleep(0.05)
        self.link.write(self.address, command(CMD_READ_MEASUREMENT))
        return decode(self.link.read(self.address, 18))


class Scd30(Readable):
    """One chip on the device root: `co2`, `temperature`, `humidity` [RP], one I2C transaction."""

    co2 = Output("co2", quantity=CO2, range=(0.0, 40000.0), precision=0)
    temperature = Output("temperature", quantity=TEMPERATURE, range=(-40.0, 70.0), precision=2)
    humidity = Output("humidity", quantity=HUMIDITY, range=(0.0, 100.0), precision=2)

    def __init__(
        self,
        name: str,
        link: I2cLink,
        address: int = SCD30_ADDRESS,
        pressure_mbar: int = 0,
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.sensor = Scd30Sensor(link, address, pressure_mbar, sleep)

    @property
    def config(self) -> Scd30Config:
        return Scd30Config(link="", address=self.sensor.address)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        co2, temperature, humidity = self.sensor.read()
        yield self.sample(time_ns, co2=co2, temperature=temperature, humidity=humidity)


class Scd30Config(DriverConfig[Scd30], tag="scd30"):
    """One chip by its I2C address, in continuous-measurement mode."""

    link: I2cLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(default=SCD30_ADDRESS, ge=0x03, le=0x77)
    pressure_mbar: int = Field(default=0, description="0 disables ambient-pressure compensation")
    sleep: bool = True

    def build(self, name: str, label: str | None = None) -> Scd30:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Scd30(name, resolve(self.link), self.address, self.pressure_mbar, self.sleep, label)


Scd30.config_type = Scd30Config


__all__ = [
    "CMD_GET_DATA_READY",
    "CMD_READ_MEASUREMENT",
    "CMD_SET_MEASUREMENT_INTERVAL",
    "CMD_START_CONTINUOUS_MEASUREMENT",
    "CMD_STOP_CONTINUOUS_MEASUREMENT",
    "CO2",
    "HUMIDITY",
    "SCD30_ADDRESS",
    "TEMPERATURE",
    "Scd30",
    "Scd30Config",
    "Scd30Sensor",
    "command",
    "crc_words",
    "decode",
    "encode",
]
