"""Generic devices for common lab equipment, configured with tables rather than code.

A SCPI meter, a Modbus controller: a reader or actuator whose behaviour is a
table of queries or registers, so it can be declared in a rig file. Vendor
packages subclass these with the table filled in.
"""

from .modbus import ModbusActuator, ModbusActuatorConfig, ModbusReader, ModbusReaderConfig, Register
from .scpi import ScpiActuator, ScpiActuatorConfig, ScpiMeasurand, ScpiReader, ScpiReaderConfig

__all__ = [
    "ModbusActuator",
    "ModbusActuatorConfig",
    "ModbusReader",
    "ModbusReaderConfig",
    "Register",
    "ScpiActuator",
    "ScpiActuatorConfig",
    "ScpiMeasurand",
    "ScpiReader",
    "ScpiReaderConfig",
]
