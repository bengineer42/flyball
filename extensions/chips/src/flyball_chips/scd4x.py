"""Sensirion SCD40/SCD41: CO2, temperature and humidity over I2C, periodic-measurement mode.

Same CRC-protected-word reply family as `scd30` (`crc_words`, reused from
there) but a different command set and timing: 16-bit commands, no
arguments here, and a 9-byte reply -- three raw 16-bit words (CO2 ppm,
temperature, humidity), each with its own CRC-8, rather than SCD30's six
words of IEEE-754 float. SCD40 and SCD41 share this protocol; SCD41 adds a
single-shot mode (`variant="scd41"`, `single_shot=True`) that SCD40 lacks.
I2C only -- unlike the SCD30, the SCD4x line has no Modbus alternative.

[Unverified] against real hardware: decoded from Sensirion's public SCD4x
datasheet and the `Sensirion/embedded-i2c-scd4x` reference source, never
run against a chip. The data-ready bitmask (lower 11 bits of the status
word) is [Unverified] beyond what the reference driver's comments show.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Literal

from flyball.foundation.config import resolve
from flyball.foundation.device import DriverConfig, Node, Readable, Readout, Sample
from flyball.foundation.errors import HardwareError
from flyball.hardware.i2c import I2cLink
from pydantic import Field

from flyball_chips._links import I2cLinkConfig
from flyball_chips._sensirion import crc8, crc_words
from flyball_chips.scd30 import CO2, HUMIDITY, TEMPERATURE

CMD_START_PERIODIC_MEASUREMENT = 0x21B1
CMD_START_LOW_POWER_PERIODIC_MEASUREMENT = 0x21AC
CMD_STOP_PERIODIC_MEASUREMENT = 0x3F86
CMD_GET_DATA_READY = 0xE4B8
CMD_READ_MEASUREMENT = 0xEC05
CMD_MEASURE_SINGLE_SHOT = 0x219D
CMD_MEASURE_SINGLE_SHOT_RHT_ONLY = 0x2196
"""Command words, from the datasheet. The single-shot pair is SCD41-only."""

SCD4X_ADDRESS = 0x62
Variant = Literal["scd40", "scd41"]

SINGLE_SHOT_S = 5.0
SINGLE_SHOT_RHT_ONLY_S = 0.05
PERIODIC_INTERVAL_S = 5.0
LOW_POWER_INTERVAL_S = 30.0
"""Maximum time between the chip refreshing its buffer, per the datasheet."""
STOP_S = 0.5
"""After stop_periodic_measurement the chip answers nothing else for 500 ms (datasheet 3.5.3)."""


def _command(word: int) -> list[int]:
    return [word >> 8, word & 0xFF]


def decode(frame: bytes) -> tuple[float, float, float]:
    """(CO2 ppm, °C, %RH) from the 9-byte reply: three raw 16-bit words.

    Raises:
        HardwareError: A CRC that does not match.
    """
    co2, raw_t, raw_h = crc_words(frame, 3)
    temperature = -45.0 + 175.0 * raw_t / 65536.0
    humidity = min(100.0, max(0.0, 100.0 * raw_h / 65536.0))
    return float(co2), temperature, humidity


def encode(co2: float, temperature: float, humidity: float) -> bytes:
    """The frame the chip would send for these values; for fakes and tests."""
    raw_t = round((temperature + 45.0) / 175.0 * 65536.0)
    raw_h = round(humidity / 100.0 * 65536.0)
    out = bytearray()
    for raw in (round(co2), raw_t, raw_h):
        word = raw.to_bytes(2, "big")
        out += word + bytes([crc8(word)])
    return bytes(out)


class Scd4xSensor:
    """One chip at `address`: starts periodic (or single-shot) measurement, then reads it."""

    __slots__ = ("address", "link", "single_shot", "sleep", "timeout_s", "variant")

    def __init__(
        self,
        link: I2cLink,
        address: int = SCD4X_ADDRESS,
        variant: Variant = "scd40",
        low_power: bool = False,
        single_shot: bool = False,
        sleep: bool = True,
    ) -> None:
        if single_shot and variant != "scd41":
            raise ValueError("single-shot mode is SCD41-only")
        self.link = link
        self.address = address
        self.variant: Variant = variant
        self.single_shot = single_shot
        self.sleep = sleep
        """Whether to wait out conversion/interval times; off against a fake."""
        self.timeout_s = LOW_POWER_INTERVAL_S if low_power else PERIODIC_INTERVAL_S
        # A chip left measuring by an earlier run refuses every start command; stop it
        # first, as Sensirion's own example does, and wait until it listens again.
        self.link.write(address, _command(CMD_STOP_PERIODIC_MEASUREMENT))
        if sleep:
            time.sleep(STOP_S)
        if not single_shot:
            start = (
                CMD_START_LOW_POWER_PERIODIC_MEASUREMENT
                if low_power
                else CMD_START_PERIODIC_MEASUREMENT
            )
            self.link.write(address, _command(start))

    def _ready(self) -> bool:
        self.link.write(self.address, _command(CMD_GET_DATA_READY))
        (status,) = crc_words(self.link.read(self.address, 3), 1)
        return (status & 0x07FF) != 0

    def read(self) -> tuple[float, float, float]:
        """(CO2 ppm, °C, %RH): one I2C transaction, after the conversion is ready."""
        if self.single_shot:
            rht_only = False
            self.link.write(
                self.address,
                _command(CMD_MEASURE_SINGLE_SHOT_RHT_ONLY if rht_only else CMD_MEASURE_SINGLE_SHOT),
            )
            if self.sleep:
                time.sleep(SINGLE_SHOT_RHT_ONLY_S if rht_only else SINGLE_SHOT_S)
        elif self.sleep:
            deadline = time.monotonic() + self.timeout_s
            while not self._ready():
                if time.monotonic() > deadline:
                    raise HardwareError(f"SCD4x measurement not ready within {self.timeout_s}s")
                time.sleep(0.05)
        self.link.write(self.address, _command(CMD_READ_MEASUREMENT))
        return decode(self.link.read(self.address, 9))


class Scd4x(Readable):
    """One chip on the device root: `co2`, `temperature`, `humidity` [RP], one I2C transaction."""

    co2 = Readout("co2", quantity=CO2, range=(0.0, 40000.0), precision=0)
    temperature = Readout("temperature", quantity=TEMPERATURE, range=(-10.0, 60.0), precision=2)
    humidity = Readout("humidity", quantity=HUMIDITY, range=(0.0, 100.0), precision=2)

    def __init__(
        self,
        name: str,
        link: I2cLink,
        address: int = SCD4X_ADDRESS,
        variant: Variant = "scd40",
        low_power: bool = False,
        single_shot: bool = False,
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.sensor = Scd4xSensor(link, address, variant, low_power, single_shot, sleep)

    @property
    def config(self) -> Scd4xConfig:
        return Scd4xConfig(
            link="",
            address=self.sensor.address,
            variant=self.sensor.variant,
            single_shot=self.sensor.single_shot,
        )

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        co2, temperature, humidity = self.sensor.read()
        yield self.sample(time_ns, co2=co2, temperature=temperature, humidity=humidity)


class Scd4xConfig(DriverConfig[Scd4x], type="scd4x"):
    """One chip by its I2C address; `variant: scd41` unlocks `single_shot`."""

    link: I2cLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(default=SCD4X_ADDRESS, ge=0x03, le=0x77)
    variant: Variant = "scd40"
    low_power: bool = False
    single_shot: bool = False
    sleep: bool = True

    def build(self, name: str, label: str | None = None) -> Scd4x:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Scd4x(
            name,
            resolve(self.link),
            self.address,
            self.variant,
            self.low_power,
            self.single_shot,
            self.sleep,
            label,
        )


Scd4x.config_type = Scd4xConfig


__all__ = [
    "CMD_GET_DATA_READY",
    "CMD_MEASURE_SINGLE_SHOT",
    "CMD_MEASURE_SINGLE_SHOT_RHT_ONLY",
    "CMD_READ_MEASUREMENT",
    "CMD_START_LOW_POWER_PERIODIC_MEASUREMENT",
    "CMD_START_PERIODIC_MEASUREMENT",
    "CMD_STOP_PERIODIC_MEASUREMENT",
    "SCD4X_ADDRESS",
    "Scd4x",
    "Scd4xConfig",
    "Scd4xSensor",
    "Variant",
    "decode",
    "encode",
]
