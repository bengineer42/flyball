"""UART over `/dev/ttyUSBn` (or similar) through pyserial."""

from __future__ import annotations

import threading

from flyball.foundation.config import Config
from flyball.hardware.uart import UartLink
from flyball_sim.links import FakeUart, FakeUartConfig
from pydantic import Field


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
