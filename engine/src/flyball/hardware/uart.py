"""The UART link protocol: write bytes, read exactly N, or read up to a terminator.

OS-independent -- no bus implementation lives here. `extensions/linux`
supplies `SerialUart` (a real `/dev/ttyUSBn`-style port through pyserial) and
`flyball-sim` supplies `FakeUart` (a scripted one for tests and hardware-free
rigs), both built from tagged configs.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class UartLink(Protocol):
    """A serial port: write bytes, read exactly N, or read up to a terminator."""

    def write(self, data: bytes) -> None: ...

    def read(self, length: int) -> bytes: ...

    def read_until(self, terminator: bytes = b"\r") -> bytes: ...


__all__ = ["UartLink"]
