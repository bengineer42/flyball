"""The I2C link protocol: register reads and writes at an address.

OS-independent -- no bus implementation lives here. `extensions/linux`
supplies `SmbusI2c` (a real `/dev/i2c-N` bus) and `FakeI2c` (a scripted one
for tests and hardware-free rigs), both built from tagged configs.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class I2cLink(Protocol):
    """A bus: register reads and writes at an address, and raw transfers for register-less chips."""

    def read_register(self, address: int, register: int, length: int) -> bytes: ...

    def write_register(self, address: int, register: int, data: Sequence[int]) -> None: ...

    def write(self, address: int, data: Sequence[int]) -> None: ...

    def read(self, address: int, length: int) -> bytes: ...


__all__ = ["I2cLink"]
