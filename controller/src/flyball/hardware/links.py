"""Links to instruments: what a generic device talks over.

Each kind is a protocol, a fake for tests and hardware-free rigs, a real
implementation that imports its driver only when built, and a tagged config
that builds it from a file. A device takes the protocol; which one it gets
is the rig's business.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from flyball.core.config import Config

# region Text links: SCPI and line-oriented serial


@runtime_checkable
class TextLink(Protocol):
    """A line-oriented instrument: write a command, or write one and read the reply."""

    def write(self, command: str) -> None: ...

    def query(self, command: str) -> str: ...


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


# endregion
# region Register links: Modbus


@runtime_checkable
class RegisterLink(Protocol):
    """A register map: read and write 16-bit holding registers."""

    def read_registers(self, address: int, count: int = 1, unit: int = 1) -> list[int]: ...

    def write_registers(self, address: int, values: list[int], unit: int = 1) -> None: ...


class FakeRegisterLink:
    """A dict of registers."""

    def __init__(self, registers: dict[int, int] | None = None) -> None:
        self.registers = dict(registers or {})
        self.writes: list[tuple[int, list[int]]] = []

    def read_registers(self, address: int, count: int = 1, unit: int = 1) -> list[int]:
        return [self.registers.get(address + i, 0) for i in range(count)]

    def write_registers(self, address: int, values: list[int], unit: int = 1) -> None:
        for i, value in enumerate(values):
            self.registers[address + i] = value
        self.writes.append((address, list(values)))


class FakeRegisterLinkConfig(Config[RegisterLink], tag="fake_registers"):
    registers: dict[int, int] = Field(default_factory=dict)

    def build(self) -> RegisterLink:
        return FakeRegisterLink(self.registers)


class ModbusLink:
    """Modbus TCP or RTU through pymodbus. Needs the `modbus` extra."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._lock = threading.Lock()
        client.connect()

    @classmethod
    def tcp(cls, host: str, port: int = 502) -> ModbusLink:
        from pymodbus.client import ModbusTcpClient

        return cls(ModbusTcpClient(host, port=port))

    @classmethod
    def rtu(cls, port: str, baud: int = 9600) -> ModbusLink:
        from pymodbus.client import ModbusSerialClient

        return cls(ModbusSerialClient(port, baudrate=baud))

    def read_registers(self, address: int, count: int = 1, unit: int = 1) -> list[int]:
        with self._lock:
            result = self._client.read_holding_registers(address, count=count, slave=unit)
        if result.isError():
            raise OSError(f"Modbus read at {address} failed: {result}")
        return list(result.registers)

    def write_registers(self, address: int, values: list[int], unit: int = 1) -> None:
        with self._lock:
            result = self._client.write_registers(address, values, slave=unit)
        if result.isError():
            raise OSError(f"Modbus write at {address} failed: {result}")


class ModbusTcpConfig(Config[RegisterLink], tag="modbus_tcp"):
    host: str
    port: int = 502

    def build(self) -> RegisterLink:
        return ModbusLink.tcp(self.host, self.port)


class ModbusRtuConfig(Config[RegisterLink], tag="modbus_rtu"):
    port: str
    baud: int = 9600

    def build(self) -> RegisterLink:
        return ModbusLink.rtu(self.port, self.baud)


# endregion

TEXT_LINKS = (FakeTextLinkConfig, VisaLinkConfig, SerialLinkConfig)
REGISTER_LINKS = (FakeRegisterLinkConfig, ModbusTcpConfig, ModbusRtuConfig)
TextLinkConfig = Config.union(*TEXT_LINKS)
"""What a device config's `link` field admits: one of these, or a name declared under `links`."""
RegisterLinkConfig = Config.union(*REGISTER_LINKS)

__all__ = [
    "REGISTER_LINKS",
    "TEXT_LINKS",
    "FakeRegisterLink",
    "FakeRegisterLinkConfig",
    "FakeTextLink",
    "FakeTextLinkConfig",
    "ModbusLink",
    "ModbusRtuConfig",
    "ModbusTcpConfig",
    "RegisterLink",
    "RegisterLinkConfig",
    "SerialLink",
    "SerialLinkConfig",
    "TextLink",
    "TextLinkConfig",
    "VisaLink",
    "VisaLinkConfig",
]
