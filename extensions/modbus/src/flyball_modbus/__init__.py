"""Modbus TCP/RTU register links and the generic `modbus` device.

Neither `ModbusLink` nor its configs import pymodbus at module load: only
building a real one does, so the `modbus` extra is needed only where a real
link is actually built. `FakeRegisterLink` needs nothing.
"""

from ._links import (
    REGISTER_LINKS,
    FakeRegisterLink,
    FakeRegisterLinkConfig,
    ModbusLink,
    ModbusRtuConfig,
    ModbusTcpConfig,
    RegisterLinkConfig,
)
from ._modbus import Modbus, ModbusConfig, ModbusRegister

__all__ = [
    "REGISTER_LINKS",
    "FakeRegisterLink",
    "FakeRegisterLinkConfig",
    "Modbus",
    "ModbusConfig",
    "ModbusLink",
    "ModbusRegister",
    "ModbusRtuConfig",
    "ModbusTcpConfig",
    "RegisterLinkConfig",
]
