"""Register links: a fake for tests, and Modbus TCP/RTU through pymodbus."""

from __future__ import annotations

import threading
from typing import Any, Literal

from flyball.foundation.config import Config
from flyball.hardware.links import RegisterLink
from pydantic import Field

Kind = Literal["holding", "input", "coil", "discrete"]
"""Which Modbus table a register lives in, and so which function code reads/writes it:
`holding` (FC03/FC16), `input` (FC04, read-only), `coil` (FC01/FC05) and `discrete`
(FC02, read-only). Coils and discrete inputs are booleans (0/1)."""

READ_ONLY_KINDS: tuple[Kind, ...] = ("input", "discrete")
BOOLEAN_KINDS: tuple[Kind, ...] = ("coil", "discrete")


class FakeRegisterLink:
    """A dict of registers, keyed by address; `kind` is recorded per call, not per address."""

    def __init__(self, registers: dict[int, int] | None = None, blocking: bool = False) -> None:
        self.registers = dict(registers or {})
        self.blocking = blocking
        """Whether a device built over this link should run its writes on the Writer thread."""
        self.reads: list[tuple[Kind, int, int]] = []
        self.writes: list[tuple[Kind, int, list[int]]] = []

    def read_registers(
        self, address: int, count: int = 1, unit: int = 1, kind: Kind = "holding"
    ) -> list[int]:
        self.reads.append((kind, address, count))
        return [self.registers.get(address + i, 0) for i in range(count)]

    def write_registers(
        self, address: int, values: list[int], unit: int = 1, kind: Kind = "holding"
    ) -> None:
        if kind in READ_ONLY_KINDS:
            raise ValueError(f"{kind} registers are read-only")
        for i, value in enumerate(values):
            self.registers[address + i] = value
        self.writes.append((kind, address, list(values)))


class FakeRegisterLinkConfig(Config[RegisterLink], type="fake_registers"):
    registers: dict[int, int] = Field(default_factory=dict)
    blocking: bool = Field(
        default=False,
        description="Run this fake's writes on the Writer thread, as a real bus would.",
    )

    def build(self) -> RegisterLink:
        return FakeRegisterLink(self.registers, blocking=self.blocking)


class ModbusLink:
    """Modbus TCP or RTU through pymodbus. Needs the `modbus` extra."""

    blocking = True

    def __init__(self, client: Any) -> None:
        self._client = client
        self._lock = threading.Lock()
        client.connect()

    @classmethod
    def tcp(cls, host: str, port: int = 502, timeout_s: float = 3.0) -> ModbusLink:
        from pymodbus.client import ModbusTcpClient

        return cls(ModbusTcpClient(host, port=port, timeout=timeout_s))

    @classmethod
    def rtu(cls, port: str, baud: int = 9600) -> ModbusLink:
        from pymodbus.client import ModbusSerialClient

        return cls(ModbusSerialClient(port, baudrate=baud))

    def read_registers(
        self, address: int, count: int = 1, unit: int = 1, kind: Kind = "holding"
    ) -> list[int]:
        reader = {
            "holding": self._client.read_holding_registers,
            "input": self._client.read_input_registers,
            "coil": self._client.read_coils,
            "discrete": self._client.read_discrete_inputs,
        }[kind]
        with self._lock:
            result = reader(address, count=count, device_id=unit)
        if result.isError():
            raise OSError(f"Modbus read at {address} failed: {result}")
        if kind in ("coil", "discrete"):
            return [int(bit) for bit in result.bits[:count]]
        return list(result.registers)

    def write_registers(
        self, address: int, values: list[int], unit: int = 1, kind: Kind = "holding"
    ) -> None:
        if kind in READ_ONLY_KINDS:
            raise ValueError(f"{kind} registers are read-only")
        with self._lock:
            if kind == "coil":
                result = self._client.write_coil(address, bool(values[0]), device_id=unit)
            else:
                result = self._client.write_registers(address, values, device_id=unit)
        if result.isError():
            raise OSError(f"Modbus write at {address} failed: {result}")


class ModbusTcpConfig(Config[RegisterLink], type="modbus_tcp"):
    host: str
    port: int = 502
    timeout_s: float = Field(default=3.0, description="Socket timeout for the pymodbus client.")

    def build(self) -> RegisterLink:
        return ModbusLink.tcp(self.host, self.port, self.timeout_s)


class ModbusRtuConfig(Config[RegisterLink], type="modbus_rtu"):
    port: str
    baud: int = 9600

    def build(self) -> RegisterLink:
        return ModbusLink.rtu(self.port, self.baud)


REGISTER_LINKS = (FakeRegisterLinkConfig, ModbusTcpConfig, ModbusRtuConfig)
RegisterLinkConfig = Config.union(*REGISTER_LINKS)
"""What `modbus`'s `link` field admits: one of these, or a name declared under `links`."""

__all__ = [
    "BOOLEAN_KINDS",
    "READ_ONLY_KINDS",
    "REGISTER_LINKS",
    "FakeRegisterLink",
    "FakeRegisterLinkConfig",
    "Kind",
    "ModbusLink",
    "ModbusRtuConfig",
    "ModbusTcpConfig",
    "RegisterLinkConfig",
]
