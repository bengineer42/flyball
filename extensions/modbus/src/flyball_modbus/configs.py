"""The entry-point target: explicitly registers every type this package provides."""

from flyball.model.catalog import Catalogs

from ._links import FakeRegisterLinkConfig, ModbusRtuConfig, ModbusTcpConfig
from ._modbus import ModbusConfig


def register(catalog: Catalogs) -> None:
    catalog.register_link(FakeRegisterLinkConfig)
    catalog.register_link(ModbusTcpConfig)
    catalog.register_link(ModbusRtuConfig)
    catalog.register_device(ModbusConfig)
