"""TE Connectivity / Silicon Labs HTU21D and Si7021: temperature and humidity over I2C.

No-hold-master command, a wait, then a poll-until-ACK read: two bytes of
data (with the two low bits carrying a measurement-type status flag, not
part of the value) and a CRC. No registers, so this is not a table. Decoded
from TE Connectivity's public "HTU21D(F) RH/T SENSOR IC" datasheet (05/2017):
the command table (p.10), the CRC-8 properties and worked examples (p.13-14),
and the temperature/humidity conversion formulas (p.14 of the amsys.de copy /
Si7021's equivalent section) together with TE's compensation-formula page.

Unlike Sensirion's SHTxx family this chip's CRC initialises at 0x00, not
0xFF -- same generator polynomial (0x31 / x^8+x^5+x^4+1), different starting
state, so the two are not interchangeable despite looking alike.

[Unverified] This module has never been run against real HTU21D or Si7021
hardware -- only against the scripted `FakeI2c` in `tests/test_htu21d.py`.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Literal

from flyball.core.config import resolve
from flyball.core.device import DriverConfig, Output, Readable
from flyball.core.errors import HardwareError
from flyball.core.quantity import Quantity
from flyball.core.signal import Node, Sample
from flyball.core.units.dimensions import Fraction
from flyball.core.units.si import Celsius
from flyball.hardware.i2c import I2cLink
from pydantic import Field

from flyball_linux.devices.chips import _sensirion
from flyball_linux.links.i2c import I2cLinkConfig

HTU21D_CRC_INIT = 0x00
"""Same generator polynomial as the Sensirion family (0x31), different initial value."""

Precision = Literal["temperature", "humidity"]
TRIGGER_TEMPERATURE_NO_HOLD = 0xF3
TRIGGER_HUMIDITY_NO_HOLD = 0xF5
SOFT_RESET = 0xFE
"""No-hold-master trigger commands and soft reset (datasheet p.10, Table)."""

MAX_WAIT_S: dict[Precision, float] = {"temperature": 0.050, "humidity": 0.016}
"""Maximum measurement duration at the (default, 14-bit/12-bit) resolution:
50 ms for temperature, 16 ms for humidity (datasheet p.3, p.5)."""

POLL_INTERVAL_S = 0.001
"""How often to retry the no-hold-master read while the sensor NACKs."""

PercentRH = Fraction.unit("percent relative humidity", "%RH", 0.01, scale=(0.0, 100.0))
HUMIDITY = Quantity("humidity", PercentRH)
TEMPERATURE = Quantity("temperature", Celsius)
HTU21D_ADDRESS = 0x40
"""The only address; there is no ADDR pin (unlike the SHT3x family)."""


def crc8(data: bytes) -> int:
    """The chip's CRC-8: polynomial 0x31 (x^8+x^5+x^4+1), initial 0x00, no final XOR.

    Datasheet worked examples: `CRC(0x683A) = 0x7C` (a humidity word),
    `CRC(0x4E85) = 0x6B` (a temperature word).
    """
    return _sensirion.crc8(data, init=HTU21D_CRC_INIT)


def decode_temperature(frame: bytes) -> float:
    """°C from a three-byte temperature reply (two data bytes, a CRC).

    Raises:
        HardwareError: A CRC that does not match.
    """
    return -46.85 + 175.72 * _raw(frame, "HTU21D temperature") / 65536.0


def decode_humidity(frame: bytes) -> float:
    """%RH from a three-byte humidity reply (two data bytes, a CRC).

    Raises:
        HardwareError: A CRC that does not match.
    """
    humidity = -6.0 + 125.0 * _raw(frame, "HTU21D humidity") / 65536.0
    return min(100.0, max(0.0, humidity))


def _raw(frame: bytes, what: str) -> int:
    if len(frame) != 3:
        raise HardwareError(f"{what} reply is {len(frame)} bytes, not 3")
    data, crc = frame[0:2], frame[2]
    if crc8(data) != crc:
        raise HardwareError(f"{what} CRC mismatch in {frame.hex()}")
    return int.from_bytes(data, "big") & 0xFFFC  # the two low bits are status, not data


def encode(value: float, *, humidity: bool) -> bytes:
    """The frame the chip would send for this value; for fakes and tests.

    The two status bits (measurement type) are left at `00`, as the
    datasheet requires them to be masked off before conversion anyway.
    """
    if humidity:
        raw = round((value + 6.0) / 125.0 * 65536.0)
    else:
        raw = round((value + 46.85) / 175.72 * 65536.0)
    data = (raw & 0xFFFC).to_bytes(2, "big")
    return data + bytes([crc8(data)])


class Htu21dSensor:
    """One chip at a fixed address: two no-hold-master transactions, one `read`."""

    __slots__ = ("address", "link", "sleep")

    def __init__(self, link: I2cLink, address: int = HTU21D_ADDRESS, sleep: bool = True) -> None:
        self.link = link
        self.address = address
        self.sleep = sleep
        """Whether to wait the conversion time; off in a test against a fake."""

    def _measure(self, command: int, precision: Precision) -> bytes:
        self.link.write(self.address, [command])
        if self.sleep:
            time.sleep(MAX_WAIT_S[precision])
        while True:
            try:
                return self.link.read(self.address, 3)
            except OSError:
                if not self.sleep:
                    raise
                time.sleep(POLL_INTERVAL_S)

    def read(self) -> tuple[float, float]:
        """(°C, %RH): two I2C transactions, no-hold-master."""
        temperature = decode_temperature(self._measure(TRIGGER_TEMPERATURE_NO_HOLD, "temperature"))
        humidity = decode_humidity(self._measure(TRIGGER_HUMIDITY_NO_HOLD, "humidity"))
        return temperature, humidity


class Htu21d(Readable):
    """One chip on the device root: `humidity`, `temperature [RP]`, two I2C transactions."""

    humidity = Output("humidity", quantity=HUMIDITY, range=(0.0, 100.0), precision=2)
    temperature = Output("temperature", quantity=TEMPERATURE, range=(-40.0, 125.0), precision=2)

    def __init__(
        self,
        name: str,
        link: I2cLink,
        address: int = HTU21D_ADDRESS,
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.sensor = Htu21dSensor(link, address, sleep)

    @property
    def config(self) -> Htu21dConfig:
        return Htu21dConfig(link="", address=self.sensor.address)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        temperature, humidity = self.sensor.read()
        yield self.sample(time_ns, humidity=humidity, temperature=temperature)


class Htu21dConfig(DriverConfig[Htu21d], tag="htu21d"):
    """One chip; there is no address pin, but the field stays for parity with sht31/sht4x."""

    link: I2cLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(default=HTU21D_ADDRESS, ge=0x03, le=0x77)

    def build(self, name: str, label: str | None = None) -> Htu21d:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Htu21d(name, resolve(self.link), self.address, label=label)


Htu21d.config_type = Htu21dConfig  # the config is declared after the device it builds


__all__ = [
    "HTU21D_ADDRESS",
    "HUMIDITY",
    "MAX_WAIT_S",
    "SOFT_RESET",
    "TEMPERATURE",
    "TRIGGER_HUMIDITY_NO_HOLD",
    "TRIGGER_TEMPERATURE_NO_HOLD",
    "Htu21d",
    "Htu21dConfig",
    "Htu21dSensor",
    "PercentRH",
    "Precision",
    "crc8",
    "decode_humidity",
    "decode_temperature",
    "encode",
]
