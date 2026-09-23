"""The entry-point target: explicitly registers every tag this package provides.

Registering is not a side effect of importing `devices`/`links`:
`discover()` calls `register(catalog)` here, and it calls
`catalog.register_*` for each one, explicitly.
"""

from flyball.model.catalog import Catalogs

from flyball_sim.devices import PlantConfig, SimDaqConfig, SimDriveConfig
from flyball_sim.links import FakeGpioConfig, FakeI2cConfig, FakeSpiConfig, FakeUartConfig


def register(catalog: Catalogs) -> None:
    catalog.register_link(PlantConfig)
    catalog.register_device(SimDaqConfig)
    catalog.register_device(SimDriveConfig)
    catalog.register_link(FakeI2cConfig)
    catalog.register_link(FakeSpiConfig)
    catalog.register_link(FakeGpioConfig)
    catalog.register_link(FakeUartConfig)
