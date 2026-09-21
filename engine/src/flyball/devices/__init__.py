"""Generic devices for common lab equipment, configured with tables rather than code.

A SCPI meter, a Modbus controller: a device whose signal tree is a table of
queries or registers declared in its own config, so it can be named in a rig
file without a bespoke driver. Vendor packages subclass these, or wrap an
instrument library directly (`extensions/pymeasure`, `extensions/qcodes`).
"""

from .modbus import Modbus, ModbusConfig, ModbusRegister
from .scpi import Scpi, ScpiConfig, ScpiSignal

__all__ = [
    "Modbus",
    "ModbusConfig",
    "ModbusRegister",
    "Scpi",
    "ScpiConfig",
    "ScpiSignal",
]
