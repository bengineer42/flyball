"""Sensirion SGP40: VOC-index-only gas sensor over I2C.

Simpler than the SGP30: commands are two bytes with no CRC on the command
itself (unlike SHT4x/SGP30 there is nothing to check on the way out), but
each reply data word IS CRC-8 protected (polynomial 0x31, initial 0xFF, the
same as the rest of the Sensirion family). There is no on-chip baseline
save/restore the way the SGP30 has one; Sensirion's VOC index algorithm
runs host-side, out of scope here.

Every `measure_raw` call takes a humidity+temperature compensation input,
so it is a parameter to `read`/`measure`, never a hidden global; the caller
(or a future VOC-index layer above this driver) is responsible for feeding
it a recent reading. Decoded from Sensirion's public datasheet; this driver
has never been run against a real chip.

[Unverified] The exact minimum wait after `measure_raw` (around 30 ms per
the datasheet's timing table) has not been re-confirmed against the primary
PDF text; treat the constant below as approximate.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from flyball.core.config import resolve
from flyball.core.device import DriverConfig, Output, Readable
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, Node, Sample
from flyball.core.units.si import Unitless
from pydantic import Field

from flyball_linux.devices.chips._sensirion import command, crc8, crc_words, word_with_crc
from flyball_linux.links.i2c import I2cLink, I2cLinkConfig

VOC_RAW = Quantity("VOC raw signal", Unitless)
SGP40_ADDRESS = 0x59

MEASURE_RAW = 0x260F
"""Writes humidity-ticks (with CRC) then temperature-ticks (with CRC); replies one word+CRC."""
HEATER_OFF = 0x3615
"""Turns the hotplate off; the sensor returns to idle. No data, no reply."""
GET_SERIAL_ID = 0x3682
"""Three words with CRC, for identification."""

DEFAULT_HUMIDITY_TICKS = 0x8000
"""No humidity input: the datasheet's default, equivalent to 50 %RH."""
DEFAULT_TEMPERATURE_TICKS = 0x6666
"""No temperature input: the datasheet's default, equivalent to 25 degC."""


def humidity_ticks(percent_rh: float) -> int:
    """%RH -> the chip's fixed-point tick, clamped to the representable range."""
    return max(0, min(0xFFFF, round(percent_rh * 65535.0 / 100.0)))


def temperature_ticks(celsius: float) -> int:
    """Celsius -> the chip's fixed-point tick, clamped to the representable range."""
    return max(0, min(0xFFFF, round((celsius + 45.0) * 65535.0 / 175.0)))


def decode(frame: bytes) -> int:
    """The raw VOC signal word from a three-byte reply.

    Raises:
        HardwareError: The frame is the wrong length or its CRC does not match.
    """
    (word,) = crc_words(frame, 1)
    return word


class Sgp40Sensor:
    """One chip at `address`: command, wait, read, decode, in one `measure`."""

    __slots__ = ("address", "link", "sleep")

    def __init__(self, link: I2cLink, address: int = SGP40_ADDRESS, sleep: bool = True) -> None:
        self.link = link
        self.address = address
        self.sleep = sleep
        """Whether to wait the conversion time; off in a test against a fake."""

    def measure(
        self, humidity_percent_rh: float | None = None, temperature_c: float | None = None
    ) -> int:
        """The raw VOC signal, compensated by the humidity/temperature given (or the defaults)."""
        h = (
            DEFAULT_HUMIDITY_TICKS
            if humidity_percent_rh is None
            else humidity_ticks(humidity_percent_rh)
        )
        t = DEFAULT_TEMPERATURE_TICKS if temperature_c is None else temperature_ticks(temperature_c)
        self.link.write(self.address, [*command(MEASURE_RAW), *word_with_crc(h), *word_with_crc(t)])
        if self.sleep:
            time.sleep(0.030)
        return decode(self.link.read(self.address, 3))

    def heater_off(self) -> None:
        """Stops the hotplate; use before power-down."""
        self.link.write(self.address, command(HEATER_OFF))


class Sgp40(Readable):
    """One chip on the device root: `voc_raw` [RP], one I2C transaction per read."""

    voc_raw = Output(
        "voc_raw", quantity=VOC_RAW, access=Access.RP, range=(0.0, 65535.0), precision=0
    )

    def __init__(
        self,
        name: str,
        link: I2cLink,
        address: int = SGP40_ADDRESS,
        sleep: bool = True,
        humidity_source: str | None = None,
        temperature_source: str | None = None,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.sensor = Sgp40Sensor(link, address, sleep)
        self.humidity_source = humidity_source
        """The signal address to read humidity compensation from, if any; wired by the rig."""
        self.temperature_source = temperature_source
        """The signal address to read temperature compensation from, if any; wired by the rig."""

    @property
    def config(self) -> Sgp40Config:
        return Sgp40Config(link="", address=self.sensor.address)

    def read(
        self,
        time_ns: int,
        node: Node | None = None,
        humidity_percent_rh: float | None = None,
        temperature_c: float | None = None,
    ) -> Iterator[Sample]:
        voc_raw = self.sensor.measure(humidity_percent_rh, temperature_c)
        yield self.sample(time_ns, voc_raw=voc_raw)


class Sgp40Config(DriverConfig[Sgp40], tag="sgp40"):
    """One chip by its I2C address."""

    link: I2cLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(default=SGP40_ADDRESS, ge=0x03, le=0x77)

    def build(self, name: str, label: str | None = None) -> Sgp40:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Sgp40(name, resolve(self.link), self.address, label=label)


Sgp40.config_type = Sgp40Config


__all__ = [
    "DEFAULT_HUMIDITY_TICKS",
    "DEFAULT_TEMPERATURE_TICKS",
    "GET_SERIAL_ID",
    "HEATER_OFF",
    "MEASURE_RAW",
    "SGP40_ADDRESS",
    "VOC_RAW",
    "Sgp40",
    "Sgp40Config",
    "Sgp40Sensor",
    "command",
    "crc8",
    "decode",
    "humidity_ticks",
    "temperature_ticks",
    "word_with_crc",
]
