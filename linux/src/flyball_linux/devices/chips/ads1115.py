"""TI ADS1115/ADS1015: 16-bit ADC over I2C, one to four single-ended channels.

Single-shot: write the config register for a channel, wait for the
conversion, read it back. Full-scale is set per reader by `gain`.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from flyball.core.config import resolve
from flyball.core.device import DeviceConfig, DeviceState
from flyball.core.reading import Measurand, Reader, Sample, Source
from flyball.core.units.dimension import Unit
from pydantic import BaseModel, Field

from flyball_linux.links.i2c import I2cLink, I2cLinkConfig

CONVERSION = 0x00
CONFIG = 0x01
FULL_SCALE: dict[float, tuple[int, float]] = {
    2 / 3: (0b000, 6.144),
    1: (0b001, 4.096),
    2: (0b010, 2.048),
    4: (0b011, 1.024),
    8: (0b100, 0.512),
    16: (0b101, 0.256),
}
"""PGA gain -> (config bits, full-scale volts)."""
_MUX_SINGLE = {0: 0b100, 1: 0b101, 2: 0b110, 3: 0b111}


def config_word(channel: int, gain: float, data_rate: int = 0b100) -> int:
    """The config register for a single-shot read of `channel` at `gain`."""
    pga, _ = FULL_SCALE[gain]
    return (
        (1 << 15)  # start a single conversion
        | (_MUX_SINGLE[channel] << 12)
        | (pga << 9)
        | (1 << 8)  # single-shot mode
        | (data_rate << 5)
        | 0b00011  # comparator disabled
    )


class Channel(BaseModel):
    """One input: what it measures, and how volts become that."""

    channel: int = Field(ge=0, le=3)
    scale: float = Field(default=1.0, description="Measurand per volt.")
    offset: float = 0.0
    unit: str = "V"
    label: str = ""
    range: tuple[float, float] | None = None
    precision: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Ads1115State(DeviceState):
    volts: dict[str, float] = field(default_factory=dict)


class Ads1115Reader(Reader):
    def __init__(
        self,
        name: str,
        link: I2cLink,
        channels: dict[str, Channel],
        address: int = 0x48,
        gain: float = 1,
        sleep: bool = True,
    ) -> None:
        if gain not in FULL_SCALE:
            raise ValueError(f"gain must be one of {sorted(FULL_SCALE)}")
        self.link = link
        self.address = address
        self.gain = gain
        self.sleep = sleep
        self.table = dict(channels)
        self.measurands = {
            key: Measurand(key, Unit.get(c.unit), c.label, c.range, c.precision)
            for key, c in channels.items()
        }
        self.source = Source(name, self.measurands.values())
        super().__init__(name, (self.source,))
        self._volts: dict[str, float] = {}

    @property
    def state(self) -> Ads1115State:
        return Ads1115State(volts=dict(self._volts))

    def _volts_on(self, channel: int) -> float:
        _, full_scale = FULL_SCALE[self.gain]
        self.link.write_register(
            self.address, CONFIG, config_word(channel, self.gain).to_bytes(2, "big")
        )
        if self.sleep:
            time.sleep(0.009)  # 128 SPS: 7.8 ms
        raw = int.from_bytes(
            self.link.read_register(self.address, CONVERSION, 2), "big", signed=True
        )
        return raw * full_scale / 32768.0

    def read(self, time_ns: int) -> Iterable[Sample]:
        values = {}
        for key, channel in self.table.items():
            volts = self._volts_on(channel.channel)
            self._volts[key] = volts
            values[self.measurands[key]] = volts * channel.scale + channel.offset
        return [Sample(self.source, self.source.next_seq(), time_ns, values)]


class Ads1115Config(DeviceConfig[Ads1115Reader], tag="ads1115"):
    """`channels = { pressure = { channel = 0, scale = 25.0, unit = "kPa" } }`."""

    name: str
    link: I2cLinkConfig | str  # type: ignore[valid-type]
    channels: dict[str, Channel]
    address: int = Field(default=0x48, ge=0x48, le=0x4B)
    gain: float = Field(default=1, description="PGA gain: 2/3, 1, 2, 4, 8 or 16.")

    def build(self) -> Ads1115Reader:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Ads1115Reader(self.name, resolve(self.link), self.channels, self.address, self.gain)


__all__ = ["FULL_SCALE", "Ads1115Config", "Ads1115Reader", "Ads1115State", "Channel", "config_word"]
