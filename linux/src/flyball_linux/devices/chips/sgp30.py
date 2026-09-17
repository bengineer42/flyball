"""Sensirion SGP30: eCO2/TVOC over I2C, with baseline save/restore for long-term accuracy.

16-bit commands, each with a baked-in CRC in the datasheet's tables (we send
the two command bytes as-is; there is no separate CRC byte on the command
itself). Data words in a reply are CRC-8 protected the same way as SHT4x
(polynomial 0x31, initial 0xFF). Decoded from Sensirion's public datasheet;
this driver has never been run against a real chip.

The IAQ algorithm needs 15 s of `measure_iaq` calls after power-up before
its output is valid, and its internal baseline drifts over hours; the
datasheet has the host read the baseline periodically (about once an hour)
and write it back after a power cycle, so `Sgp30Sensor.get_baseline` /
`set_baseline` are part of the API, not an omission.

[Unverified] The write order of the two baseline words in `set_baseline`
(CO2eq then TVOC, mirroring the order `get_baseline` returns them) follows
the common convention of Sensirion's own embedded-sgp driver rather than a
directly re-read datasheet table; treat it as unconfirmed.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import NamedTuple

from flyball.core.config import resolve
from flyball.core.device import DriverConfig, Output, Readable
from flyball.core.errors import HardwareError
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, Node, Sample
from flyball.core.units.si import PartsPerBillion, PartsPerMillion
from pydantic import Field

from flyball_linux.links.i2c import I2cLink, I2cLinkConfig

CO2EQ = Quantity("CO2 equivalent", PartsPerMillion)
TVOC = Quantity("total VOC", PartsPerBillion)
SGP30_ADDRESS = 0x58

INIT_AIR_QUALITY = 0x2003
"""Starts the IAQ algorithm; no data, no reply. Wait >= 10 ms before the first measure."""
MEASURE_IAQ = 0x2008
"""CO2eq and TVOC, one word each with CRC. Wait >= 12 ms."""
GET_IAQ_BASELINE = 0x2015
"""CO2eq baseline then TVOC baseline, one word each with CRC. Wait >= 10 ms."""
SET_IAQ_BASELINE = 0x201E
"""Writes CO2eq baseline then TVOC baseline, each word followed by its CRC."""
SET_ABSOLUTE_HUMIDITY = 0x2061
"""Writes one word (8.8 fixed-point g/m^3) with CRC; 0x0000 reverts to no compensation."""
GET_SERIAL_ID = 0x3682
"""Three words with CRC, for identification."""


def crc8(data: bytes) -> int:
    """Sensirion's CRC-8: polynomial 0x31, initial 0xFF. Shared across their sensor family."""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def command(code: int) -> list[int]:
    """The two command bytes, MSB first."""
    return [code >> 8, code & 0xFF]


def word_with_crc(value: int) -> list[int]:
    """A 16-bit value as its two bytes plus the CRC of those two bytes."""
    data = [(value >> 8) & 0xFF, value & 0xFF]
    return [*data, crc8(bytes(data))]


class Baseline(NamedTuple):
    """The two IAQ baseline words, as the chip reads and writes them."""

    co2eq: int
    tvoc: int


def decode_words(frame: bytes, count: int) -> list[int]:
    """`count` CRC-checked 16-bit words from a reply.

    Raises:
        HardwareError: The frame is the wrong length or a CRC does not match.
    """
    if len(frame) != count * 3:
        raise HardwareError(f"SGP30 reply is {len(frame)} bytes, not {count * 3}")
    words = []
    for i in range(count):
        word = frame[i * 3 : i * 3 + 2]
        crc = frame[i * 3 + 2]
        if crc8(word) != crc:
            raise HardwareError(f"SGP30 CRC mismatch in {frame.hex()}")
        words.append(int.from_bytes(word, "big"))
    return words


class Sgp30Sensor:
    """One chip at `address`: IAQ init, measure, and baseline get/set."""

    __slots__ = ("address", "link", "sleep")

    def __init__(self, link: I2cLink, address: int = SGP30_ADDRESS, sleep: bool = True) -> None:
        self.link = link
        self.address = address
        self.sleep = sleep
        """Whether to wait the conversion time; off in a test against a fake."""

    def _wait(self, seconds: float) -> None:
        if self.sleep:
            time.sleep(seconds)

    def init_air_quality(self) -> None:
        """Starts the on-chip IAQ algorithm. Call once before the first `measure`."""
        self.link.write(self.address, command(INIT_AIR_QUALITY))
        self._wait(0.010)

    def measure(self) -> tuple[int, int]:
        """(CO2eq ppm, TVOC ppb): one I2C transaction."""
        self.link.write(self.address, command(MEASURE_IAQ))
        self._wait(0.012)
        co2eq, tvoc = decode_words(self.link.read(self.address, 6), 2)
        return co2eq, tvoc

    def get_baseline(self) -> Baseline:
        """The current CO2eq/TVOC baseline, to persist across power cycles."""
        self.link.write(self.address, command(GET_IAQ_BASELINE))
        self._wait(0.010)
        co2eq, tvoc = decode_words(self.link.read(self.address, 6), 2)
        return Baseline(co2eq, tvoc)

    def set_baseline(self, baseline: Baseline) -> None:
        """Restores a baseline read earlier, typically just after `init_air_quality`."""
        self.link.write(
            self.address,
            [
                *command(SET_IAQ_BASELINE),
                *word_with_crc(baseline.co2eq),
                *word_with_crc(baseline.tvoc),
            ],
        )

    def set_absolute_humidity(self, grams_per_m3: float | None) -> None:
        """Humidity compensation input; `None` turns compensation off (writes 0x0000)."""
        raw = 0 if grams_per_m3 is None else round(grams_per_m3 * 256.0)
        raw = max(0, min(0xFFFF, raw))
        self.link.write(self.address, [*command(SET_ABSOLUTE_HUMIDITY), *word_with_crc(raw)])


class Sgp30(Readable):
    """One chip on the device root: `co2eq`, `tvoc` [RP], one I2C transaction per measure."""

    co2eq = Output("co2eq", quantity=CO2EQ, access=Access.RP, range=(400.0, 60000.0), precision=0)
    tvoc = Output("tvoc", quantity=TVOC, access=Access.RP, range=(0.0, 60000.0), precision=0)

    def __init__(
        self,
        name: str,
        link: I2cLink,
        address: int = SGP30_ADDRESS,
        sleep: bool = True,
        baseline: Baseline | None = None,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.sensor = Sgp30Sensor(link, address, sleep)
        self.sensor.init_air_quality()
        if baseline is not None:
            self.sensor.set_baseline(baseline)

    @property
    def config(self) -> Sgp30Config:
        return Sgp30Config(link="", address=self.sensor.address)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        co2eq, tvoc = self.sensor.measure()
        yield self.sample(time_ns, co2eq=co2eq, tvoc=tvoc)


class Sgp30Config(DriverConfig[Sgp30], tag="sgp30"):
    """One chip by its I2C address."""

    link: I2cLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(default=SGP30_ADDRESS, ge=0x03, le=0x77)

    def build(self, name: str, label: str | None = None) -> Sgp30:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Sgp30(name, resolve(self.link), self.address, label=label)


Sgp30.config_type = Sgp30Config


__all__ = [
    "CO2EQ",
    "GET_IAQ_BASELINE",
    "GET_SERIAL_ID",
    "INIT_AIR_QUALITY",
    "MEASURE_IAQ",
    "SET_ABSOLUTE_HUMIDITY",
    "SET_IAQ_BASELINE",
    "SGP30_ADDRESS",
    "TVOC",
    "Baseline",
    "Sgp30",
    "Sgp30Config",
    "Sgp30Sensor",
    "command",
    "crc8",
    "decode_words",
    "word_with_crc",
]
