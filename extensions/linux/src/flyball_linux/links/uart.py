"""UART over `/dev/ttyUSBn` (or similar) through pyserial."""

from __future__ import annotations

import threading

from flyball.foundation.config import Config
from flyball.hardware.uart import UartLink
from flyball_sim.links import FakeUart, FakeUartConfig
from pydantic import Field, field_validator


def _validated_port(port: str) -> str:
    """`port`, or raise if it is empty or cannot be a device path.

    Raises:
        ValueError: `port` is empty/whitespace-only, or contains a NUL or
            newline byte -- neither of which any real device path can.
    """
    if not port.strip():
        raise ValueError("port must not be empty")
    if any(c in port for c in ("\x00", "\n", "\r")):
        raise ValueError(f"port {port!r} is not a valid device path")
    return port


class SerialUart:
    """A real serial port through pyserial. Needs the `serial` extra."""

    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 1.0) -> None:
        import serial

        self.port = _validated_port(port)
        self._serial = serial.Serial(port=self.port, baudrate=baudrate, timeout=timeout)
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


class SerialConfig(Config[UartLink], type="uart"):
    """A kernel serial device: `port = "/dev/ttyUSB0"`."""

    port: str
    baudrate: int = Field(default=9600, gt=0)
    timeout: float = Field(default=1.0, gt=0)

    @field_validator("port")
    @classmethod
    def _port_is_a_device_path(cls, value: str) -> str:
        return _validated_port(value)

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
