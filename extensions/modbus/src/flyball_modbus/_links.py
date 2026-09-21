"""Register links: a fake for tests, and Modbus TCP/RTU through pymodbus."""

from __future__ import annotations

import threading
from typing import Any

from flyball.foundation.config import Config
from flyball.hardware.links import RegisterLink
from pydantic import Field


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


REGISTER_LINKS = (FakeRegisterLinkConfig, ModbusTcpConfig, ModbusRtuConfig)
RegisterLinkConfig = Config.union(*REGISTER_LINKS)
"""What `modbus`'s `link` field admits: one of these, or a name declared under `links`."""

__all__ = [
    "REGISTER_LINKS",
    "FakeRegisterLink",
    "FakeRegisterLinkConfig",
    "ModbusLink",
    "ModbusRtuConfig",
    "ModbusTcpConfig",
    "RegisterLinkConfig",
]
