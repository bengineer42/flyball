"""Microchip MCP3008: 10-bit, eight-channel ADC over SPI. The classic Pi ADC.

Each declared channel is one `[RP]` signal, `value = volts * scale + offset`
with `volts = count * vref / 1023`.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from flyball.core.config import resolve
from flyball.core.device import DriverConfig, Readable
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, Node, Sample, Signal, SignalSpec
from flyball.hardware.scan import Scan
from pydantic import BaseModel, ConfigDict, Field

from flyball_linux.links.spi import SpiLink, SpiLinkConfig


def request(channel: int) -> list[int]:
    """The three bytes that ask for a single-ended read of `channel`."""
    return [0x01, (0x08 | channel) << 4, 0x00]


def decode(reply: bytes) -> int:
    """The 10-bit count from the three bytes back."""
    return ((reply[1] & 0x03) << 8) | reply[2]


class Channel(BaseModel):
    """One input as a signal: which pin, what it measures, and how volts become that."""

    model_config = ConfigDict(extra="forbid")

    channel: int = Field(ge=0, le=7)
    quantity: str | None = Field(
        default=None, description="The quantity's own name, if it differs from the signal's."
    )
    unit: str = "V"
    scale: float = Field(default=1.0, description="Signal units per volt.")
    offset: float = 0.0


class Mcp3008(Readable):
    """Each of `channels` becomes one `[RP]` signal, one SPI transfer each when due."""

    def __init__(
        self,
        name: str,
        link: SpiLink,
        channels: Mapping[str, Channel],
        vref: float = 3.3,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        if not channels:
            raise ValueError(f"{name}: an mcp3008 reads at least one channel")
        self.link = link
        self.vref = vref
        self.channels = dict(channels)
        self._scan = Scan()
        self.bind([
            SignalSpec(name=key, quantity=Quantity(c.quantity or key, c.unit), access=Access.RP)
            for key, c in self.channels.items()
        ])
        self._signals = [self.signals[key] for key in self.channels]

    @property
    def config(self) -> Mcp3008Config:
        return Mcp3008Config(link="", channels=self.channels, vref=self.vref)

    def _value(self, signal: Signal) -> float:
        channel = self.channels[signal.name]
        count = decode(self.link.transfer(request(channel.channel)))
        return count * self.vref / 1023.0 * channel.scale + channel.offset

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """One transfer per due signal, all in one sample: microseconds apart on one chip."""
        due = self._scan.due(self._signals, time_ns, whole=node is not None)
        if due:
            yield Sample(self.root, time_ns, {signal: self._value(signal) for signal in due})


class Mcp3008Config(DriverConfig[Mcp3008], tag="mcp3008"):
    """`channels: { level: { channel: 0 } }`, in volts unless `unit` and `scale` say otherwise."""

    link: SpiLinkConfig | str  # type: ignore[valid-type]
    channels: dict[str, Channel]
    vref: float = Field(default=3.3, gt=0, description="The reference voltage on VREF.")

    def build(self, name: str, label: str | None = None) -> Mcp3008:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Mcp3008(name, resolve(self.link), self.channels, self.vref, label=label)


Mcp3008.config_type = Mcp3008Config  # the config is declared after the device it builds


__all__ = ["Channel", "Mcp3008", "Mcp3008Config", "decode", "request"]
