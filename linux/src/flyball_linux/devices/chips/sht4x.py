"""Sensirion SHT40/41/45: temperature and humidity over I2C.

Command byte, a wait, six bytes back: two of temperature, a CRC, two of
humidity, a CRC. No registers, so this is not a table.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from flyball.core.config import resolve
from flyball.core.device import DeviceConfig, DeviceState
from flyball.core.errors import HardwareError
from flyball.core.reading import Measurand, Reader, Sample, Source
from flyball.core.units.dimension import DIMENSIONLESS, Unit
from pydantic import Field

from flyball_linux.links.i2c import I2cLink, I2cLinkConfig

Precision = Literal["high", "medium", "low"]
COMMANDS: dict[str, tuple[int, float]] = {
    "high": (0xFD, 0.0083),
    "medium": (0xF6, 0.0045),
    "low": (0xE0, 0.0016),
}
"""Measure command and the datasheet's maximum conversion time, by precision."""

PercentRH = DIMENSIONLESS.unit("percent relative humidity", "%RH", 0.01)


def crc8(data: bytes) -> int:
    """Sensirion's CRC-8: polynomial 0x31, initial 0xFF."""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def decode(frame: bytes) -> tuple[float, float]:
    """(°C, %RH) from the six-byte reply.

    Raises:
        HardwareError: A CRC that does not match.
    """
    if len(frame) != 6:
        raise HardwareError(f"SHT4x reply is {len(frame)} bytes, not 6")
    for word, crc in ((frame[0:2], frame[2]), (frame[3:5], frame[5])):
        if crc8(word) != crc:
            raise HardwareError(f"SHT4x CRC mismatch in {frame.hex()}")
    raw_t = int.from_bytes(frame[0:2], "big")
    raw_h = int.from_bytes(frame[3:5], "big")
    temperature = -45.0 + 175.0 * raw_t / 65535.0
    humidity = min(100.0, max(0.0, -6.0 + 125.0 * raw_h / 65535.0))
    return temperature, humidity


def encode(temperature: float, humidity: float) -> bytes:
    """The frame the chip would send for these values; for fakes and tests."""
    raw_t = round((temperature + 45.0) / 175.0 * 65535.0)
    raw_h = round((humidity + 6.0) / 125.0 * 65535.0)
    t = raw_t.to_bytes(2, "big")
    h = raw_h.to_bytes(2, "big")
    return t + bytes([crc8(t)]) + h + bytes([crc8(h)])


@dataclass(frozen=True, slots=True, kw_only=True)
class Sht4xState(DeviceState):
    temperature: float | None = None
    humidity: float | None = None


class Sht4xReader(Reader):
    """One chip at `address` (0x44, or 0x45 on the -B variants), two measurands."""

    def __init__(
        self,
        name: str,
        link: I2cLink,
        address: int = 0x44,
        precision: Precision = "high",
        sleep: bool = True,
    ) -> None:
        self.link = link
        self.address = address
        self.precision = precision
        self.sleep = sleep
        self.temperature = Measurand(
            "temperature", Unit.get("°C"), range=(-40.0, 125.0), precision=2
        )
        self.humidity = Measurand("humidity", PercentRH, range=(0.0, 100.0), precision=2)
        self.source = Source(name, (self.temperature, self.humidity))
        super().__init__(name, (self.source,))
        self._last: tuple[float, float] | None = None

    @property
    def state(self) -> Sht4xState:
        t, h = self._last if self._last is not None else (None, None)
        return Sht4xState(temperature=t, humidity=h)

    def read(self, time_ns: int) -> Iterable[Sample]:
        command, wait_s = COMMANDS[self.precision]
        self.link.write(self.address, [command])
        if self.sleep:
            time.sleep(wait_s)
        temperature, humidity = decode(self.link.read(self.address, 6))
        self._last = (temperature, humidity)
        return [
            Sample(
                self.source,
                self.source.next_seq(),
                time_ns,
                {self.temperature: temperature, self.humidity: humidity},
            )
        ]


class Sht4xConfig(DeviceConfig[Sht4xReader], tag="sht4x"):
    name: str
    link: I2cLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(default=0x44, ge=0x03, le=0x77)
    precision: Precision = "high"

    def build(self) -> Sht4xReader:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Sht4xReader(self.name, resolve(self.link), self.address, self.precision)


__all__ = ["COMMANDS", "Sht4xConfig", "Sht4xReader", "Sht4xState", "crc8", "decode", "encode"]
