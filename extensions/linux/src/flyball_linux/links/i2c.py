"""I2C over `/dev/i2c-N` through smbus2."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any

from flyball.core.config import Config
from flyball.hardware.i2c import I2cLink
from flyball_sim.links import FakeI2c, FakeI2cConfig
from pydantic import Field


class SmbusI2c:
    """`/dev/i2c-<bus>` through smbus2. Needs the `i2c` extra.

    One lock per bus: two devices on it cannot interleave a register write
    with another's read.
    """

    def __init__(self, bus: int) -> None:
        from smbus2 import SMBus

        self.bus = bus
        self._bus = SMBus(bus)
        self._lock = threading.Lock()

    def read_register(self, address: int, register: int, length: int) -> bytes:
        with self._lock:
            return bytes(self._bus.read_i2c_block_data(address, register, length))

    def write_register(self, address: int, register: int, data: Sequence[int]) -> None:
        with self._lock:
            self._bus.write_i2c_block_data(address, register, list(data))

    def write(self, address: int, data: Sequence[int]) -> None:
        from smbus2 import i2c_msg

        with self._lock:
            self._bus.i2c_rdwr(i2c_msg.write(address, list(data)))

    def read(self, address: int, length: int) -> bytes:
        from smbus2 import i2c_msg

        with self._lock:
            message: Any = i2c_msg.read(address, length)
            self._bus.i2c_rdwr(message)
            return bytes(message)

    def close(self) -> None:
        self._bus.close()


class I2cConfig(Config[I2cLink], tag="i2c"):
    """A kernel I2C bus: `bus = 1` is `/dev/i2c-1`."""

    bus: int = Field(ge=0)

    def build(self) -> I2cLink:
        return SmbusI2c(self.bus)


I2C_LINKS = (FakeI2cConfig, I2cConfig)
I2cLinkConfig = Config.union(*I2C_LINKS)

__all__ = [
    "I2C_LINKS",
    "FakeI2c",
    "FakeI2cConfig",
    "I2cConfig",
    "I2cLink",
    "I2cLinkConfig",
    "SmbusI2c",
]
