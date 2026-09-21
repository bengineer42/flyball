"""UART over `/dev/ttyUSBn` (or similar) through pyserial."""

from __future__ import annotations

import threading

from flyball.core.config import Config
from flyball.hardware.uart import UartLink
from pydantic import Field


class FakeUart:
    r"""Scripted replies, in order; every write is kept.

    `replies = [b"1.23\r", b"OK\r"]` answers the first two reads/read_untils in turn.
    A single-element list repeats forever, matching `FakeI2c`. Reads and `read_until`
    calls draw from the same queue.
    """

    def __init__(self, replies: list[bytes] | None = None) -> None:
        self.replies = list(replies or [])
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(bytes(data))

    def _next(self) -> bytes:
        if not self.replies:
            raise OSError("no reply scripted")
        return self.replies[0] if len(self.replies) == 1 else self.replies.pop(0)

    def read(self, length: int) -> bytes:
        return self._next()[:length]

    def read_until(self, terminator: bytes = b"\r") -> bytes:
        reply = self._next()
        index = reply.find(terminator)
        return reply if index == -1 else reply[: index + len(terminator)]


class FakeUartConfig(Config[UartLink], tag="fake_uart"):
    """A scripted port, for a rig file that runs without hardware."""

    replies: list[bytes] = Field(default_factory=list)

    def build(self) -> UartLink:
        return FakeUart(self.replies)


class SerialUart:
    """A real serial port through pyserial. Needs the `serial` extra."""

    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 1.0) -> None:
        import serial

        self.port = port
        self._serial = serial.Serial(port=port, baudrate=baudrate, timeout=timeout)
        self._lock = threading.Lock()

    def write(self, data: bytes) -> None:
        with self._lock:
            self._serial.write(data)

    def read(self, length: int) -> bytes:
        with self._lock:
            return self._serial.read(length)

    def read_until(self, terminator: bytes = b"\r") -> bytes:
        with self._lock:
            return self._serial.read_until(terminator)

    def close(self) -> None:
        self._serial.close()


class SerialConfig(Config[UartLink], tag="uart"):
    """A kernel serial device: `port = "/dev/ttyUSB0"`."""

    port: str
    baudrate: int = Field(default=9600, gt=0)
    timeout: float = Field(default=1.0, gt=0)

    def build(self) -> UartLink:
        return SerialUart(self.port, self.baudrate, self.timeout)


UART_LINKS = (FakeUartConfig, SerialConfig)
UartLinkConfig = Config.union(*UART_LINKS)

__all__ = [
    "UART_LINKS",
    "FakeUart",
    "FakeUartConfig",
    "SerialConfig",
    "SerialUart",
    "UartLink",
    "UartLinkConfig",
]
