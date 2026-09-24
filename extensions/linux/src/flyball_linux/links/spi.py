"""SPI over `/dev/spidevN.M` through spidev."""

from __future__ import annotations

import threading
from collections.abc import Sequence

from flyball.foundation.config import Config
from flyball.hardware.spi import SpiLink
from flyball_sim.links import FakeSpi, FakeSpiConfig
from pydantic import Field


class SpidevSpi:
    """`/dev/spidev<bus>.<device>` through spidev. Needs the `spi` extra."""

    def __init__(
        self, bus: int, chip_select: int, speed_hz: int = 1_000_000, spi_mode: int = 0
    ) -> None:
        import spidev

        self._spi = spidev.SpiDev()
        self._spi.open(bus, chip_select)
        self._spi.max_speed_hz = speed_hz
        self._spi.mode = spi_mode
        self._lock = threading.Lock()

    def transfer(self, data: Sequence[int]) -> bytes:
        with self._lock:
            return bytes(self._spi.xfer2(list(data)))

    def close(self) -> None:
        self._spi.close()


class SpiConfig(Config[SpiLink], type="spi"):
    """A kernel SPI device: `bus = 0, chip_select = 0` is `/dev/spidev0.0`."""

    bus: int = Field(ge=0)
    chip_select: int = Field(ge=0)
    speed_hz: int = Field(default=1_000_000, gt=0)
    spi_mode: int = Field(default=0, ge=0, le=3)

    def build(self) -> SpiLink:
        return SpidevSpi(self.bus, self.chip_select, self.speed_hz, self.spi_mode)


SPI_LINKS = (FakeSpiConfig, SpiConfig)
SpiLinkConfig = Config.union(*SPI_LINKS)

__all__ = [
    "SPI_LINKS",
    "FakeSpi",
    "FakeSpiConfig",
    "SpiConfig",
    "SpiLink",
    "SpiLinkConfig",
    "SpidevSpi",
]
