"""TI ADS1115/ADS1015: 16-bit ADC over I2C, one to four single-ended channels.

Single-shot: write the config register for a channel, wait for the
conversion, read it back. Full-scale is set per device by `gain`; each
declared channel is one `[RP]` signal, `value = volts * scale + offset`.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

from flyball.core.config import resolve
from flyball.core.device import Device, DeviceState, DriverConfig
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, Node, Sample, Signal, SignalSpec
from pydantic import BaseModel, ConfigDict, Field

from flyball_linux.devices.scan import Scan
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
    """One input as a signal: which pin, what it measures, and how volts become that.

    Range, precision and bands are not here -- they are the envelope's
    `signals:` overrides, the same for every driver.
    """

    model_config = ConfigDict(extra="forbid")

    channel: int = Field(ge=0, le=3)
    quantity: str | None = Field(
        default=None, description="The quantity's own name, if it differs from the signal's."
    )
    unit: str = "V"
    scale: float = Field(default=1.0, description="Signal units per volt.")
    offset: float = 0.0


@dataclass(frozen=True, slots=True, kw_only=True)
class Ads1115State(DeviceState):
    volts: dict[str, float] = field(default_factory=dict)
    """What each channel last read at the pin, by signal name; one not yet read is absent."""


class Ads1115(Device):
    """Each of `channels` becomes one `[RP]` signal, read in turn when due."""

    def __init__(
        self,
        name: str,
        link: I2cLink,
        channels: Mapping[str, Channel],
        address: int = 0x48,
        gain: float = 1,
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        if gain not in FULL_SCALE:
            raise ValueError(f"gain must be one of {sorted(FULL_SCALE)}")
        if not channels:
            raise ValueError(f"{name}: an ads1115 reads at least one channel")
        self.link = link
        self.address = address
        self.gain = gain
        self.sleep = sleep
        """Whether to wait the conversion time; off in a test against a fake."""
        self.channels = dict(channels)
        self._scan = Scan()
        self._volts: dict[str, float] = {}
        self.bind([
            SignalSpec(name=key, quantity=Quantity(c.quantity or key, c.unit), access=Access.RP)
            for key, c in self.channels.items()
        ])

    @property
    def config(self) -> Ads1115Config:
        return Ads1115Config(link="", channels=self.channels, address=self.address, gain=self.gain)

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

    def _value(self, signal: Signal) -> float:
        channel = self.channels[signal.name]
        volts = self._volts[signal.name] = self._volts_on(channel.channel)
        return volts * channel.scale + channel.offset

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """One conversion per due signal, all in one sample: milliseconds apart on one chip."""
        due = self._scan.due(self.root if node is None else node, time_ns, whole=node is not None)
        if due:
            yield Sample(self.root, time_ns, {signal: self._value(signal) for signal in due})


class Ads1115Config(DriverConfig[Ads1115], tag="ads1115"):
    """`channels: { pressure: { channel: 0, scale: 25.0, unit: kPa } }`."""

    link: I2cLinkConfig | str  # type: ignore[valid-type]
    channels: dict[str, Channel]
    address: int = Field(default=0x48, ge=0x48, le=0x4B)
    gain: float = Field(default=1, description="PGA gain: 2/3, 1, 2, 4, 8 or 16.")

    def build(self, name: str, label: str | None = None) -> Ads1115:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Ads1115(
            name, resolve(self.link), self.channels, self.address, self.gain, label=label
        )


Ads1115.config_type = Ads1115Config  # the config is declared after the device it builds


__all__ = ["FULL_SCALE", "Ads1115", "Ads1115Config", "Ads1115State", "Channel", "config_word"]
