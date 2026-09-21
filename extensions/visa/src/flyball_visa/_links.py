"""Text links: a fake for tests, VISA through pyvisa, and a raw serial port."""

from __future__ import annotations

import threading
from collections.abc import Callable

from flyball.core.config import Config
from flyball.hardware.links import TextLink
from pydantic import Field


class FakeTextLink:
    """Answers from a table or a function; remembers every write and query."""

    def __init__(self, replies: dict[str, str] | Callable[[str], str] | None = None) -> None:
        self.replies = replies or {}
        self.written: list[str] = []
        self.queried: list[str] = []

    def write(self, command: str) -> None:
        self.written.append(command)

    def query(self, command: str) -> str:
        self.queried.append(command)
        if callable(self.replies):
            return self.replies(command)
        try:
            return self.replies[command]
        except KeyError:
            raise OSError(f"no reply for {command!r}") from None


class FakeTextLinkConfig(Config[TextLink], tag="fake_text"):
    """A scripted instrument, for a rig file that runs without hardware."""

    replies: dict[str, str] = Field(default_factory=dict)

    def build(self) -> TextLink:
        return FakeTextLink(self.replies)


class VisaLink:
    """A VISA resource through pyvisa. Needs the `visa` extra.

    One lock per link, so a reader and an actuator sharing an instrument
    cannot interleave a query with a write.
    """

    def __init__(self, resource: str, timeout_ms: int = 2000, backend: str = "@py") -> None:
        import pyvisa
        from pyvisa.resources import MessageBasedResource

        instrument = pyvisa.ResourceManager(backend).open_resource(resource)
        if not isinstance(instrument, MessageBasedResource):
            raise TypeError(f"{resource} is not a message-based (text) instrument")
        self._instrument = instrument
        self._instrument.timeout = timeout_ms
        self._lock = threading.Lock()

    def write(self, command: str) -> None:
        with self._lock:
            self._instrument.write(command)

    def query(self, command: str) -> str:
        with self._lock:
            return str(self._instrument.query(command)).strip()


class VisaLinkConfig(Config[TextLink], tag="visa"):
    """`TCPIP::192.168.1.20::INSTR`, `USB0::…::INSTR`, `ASRL/dev/ttyUSB0::INSTR`."""

    resource: str
    timeout_ms: int = 2000
    backend: str = "@py"

    def build(self) -> TextLink:
        return VisaLink(self.resource, self.timeout_ms, self.backend)


class SerialLink:
    """A serial port through pyserial, one command per line. Needs the `serial` extra."""

    def __init__(
        self, port: str, baud: int = 9600, terminator: str = "\n", timeout_s: float = 1.0
    ) -> None:
        import serial

        self._port = serial.Serial(port, baud, timeout=timeout_s)
        self._terminator = terminator
        self._lock = threading.Lock()

    def write(self, command: str) -> None:
        with self._lock:
            self._port.write((command + self._terminator).encode())

    def query(self, command: str) -> str:
        with self._lock:
            self._port.write((command + self._terminator).encode())
            return self._port.read_until(self._terminator.encode()).decode().strip()


class SerialLinkConfig(Config[TextLink], tag="serial"):
    port: str
    baud: int = 9600
    terminator: str = "\n"
    timeout_s: float = 1.0

    def build(self) -> TextLink:
        return SerialLink(self.port, self.baud, self.terminator, self.timeout_s)


TEXT_LINKS = (FakeTextLinkConfig, VisaLinkConfig, SerialLinkConfig)
TextLinkConfig = Config.union(*TEXT_LINKS)
"""What `scpi`'s `link` field admits: one of these, or a name declared under `links`."""

__all__ = [
    "TEXT_LINKS",
    "FakeTextLink",
    "FakeTextLinkConfig",
    "SerialLink",
    "SerialLinkConfig",
    "TextLinkConfig",
    "VisaLink",
    "VisaLinkConfig",
]
