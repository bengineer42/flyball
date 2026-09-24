"""Modbus TCP/RTU register links and the generic `modbus` device.

Neither `ModbusLink` nor its configs import pymodbus at module load: only
building a real one does, so the `modbus` extra is needed only where a real
link is actually built. `FakeRegisterLink` needs nothing.
"""

from ._links import (
    REGISTER_LINKS,
    FakeRegisterLink,
    FakeRegisterLinkConfig,
    Kind,
    ModbusLink,
    ModbusRtuConfig,
    ModbusTcpConfig,
    RegisterLinkConfig,
)
from ._modbus import Format, Modbus, ModbusConfig, ModbusRegister, WordOrder

__all__ = [
    "REGISTER_LINKS",
    "FakeRegisterLink",
    "FakeRegisterLinkConfig",
    "Format",
    "Kind",
    "Modbus",
    "ModbusConfig",
    "ModbusLink",
    "ModbusRegister",
    "ModbusRtuConfig",
    "ModbusTcpConfig",
    "RegisterLinkConfig",
    "WordOrder",
]
