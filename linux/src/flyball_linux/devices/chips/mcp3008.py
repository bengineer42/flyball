"""Microchip MCP3008: 10-bit, eight-channel ADC over SPI. The classic Pi ADC."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from flyball.core.config import resolve
from flyball.core.device import DeviceConfig, DeviceState
from flyball.core.reading import Measurand, Reader, Sample, Source
from flyball.core.units.dimension import Unit
from pydantic import BaseModel, Field

from flyball_linux.links.spi import SpiLink, SpiLinkConfig


def request(channel: int) -> list[int]:
    """The three bytes that ask for a single-ended read of `channel`."""
    return [0x01, (0x08 | channel) << 4, 0x00]


def decode(reply: bytes) -> int:
    """The 10-bit count from the three bytes back."""
    return ((reply[1] & 0x03) << 8) | reply[2]


class Channel(BaseModel):
    channel: int = Field(ge=0, le=7)
    scale: float = Field(default=1.0, description="Measurand per volt.")
    offset: float = 0.0
    unit: str = "V"
    label: str = ""
    range: tuple[float, float] | None = None
    precision: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Mcp3008State(DeviceState):
    counts: dict[str, int] = field(default_factory=dict)


class Mcp3008Reader(Reader):
    def __init__(
        self, name: str, link: SpiLink, channels: dict[str, Channel], vref: float = 3.3
    ) -> None:
        self.link = link
        self.vref = vref
        self.table = dict(channels)
        self.measurands = {
            key: Measurand(key, Unit.get(c.unit), c.label, c.range, c.precision)
            for key, c in channels.items()
        }
        self.source = Source(name, self.measurands.values())
        super().__init__(name, (self.source,))
        self._counts: dict[str, int] = {}

    @property
    def state(self) -> Mcp3008State:
        return Mcp3008State(counts=dict(self._counts))

    def read(self, time_ns: int) -> Iterable[Sample]:
        values = {}
        for key, channel in self.table.items():
            count = decode(self.link.transfer(request(channel.channel)))
            self._counts[key] = count
            volts = count * self.vref / 1023.0
            values[self.measurands[key]] = volts * channel.scale + channel.offset
        return [Sample(self.source, self.source.next_seq(), time_ns, values)]


class Mcp3008Config(DeviceConfig[Mcp3008Reader], tag="mcp3008"):
    name: str
    link: SpiLinkConfig | str  # type: ignore[valid-type]
    channels: dict[str, Channel]
    vref: float = Field(default=3.3, gt=0, description="The reference voltage on VREF.")

    def build(self) -> Mcp3008Reader:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Mcp3008Reader(self.name, resolve(self.link), self.channels, self.vref)


__all__ = ["Channel", "Mcp3008Config", "Mcp3008Reader", "Mcp3008State", "decode", "request"]
