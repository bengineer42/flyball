"""The entry-point target: explicitly registers every type this package provides."""

from flyball.model.catalog import Catalogs

from flyball_chips.ads1115 import Ads1115Config
from flyball_chips.bme280 import Bme280Config
from flyball_chips.ccs811 import Ccs811Config
from flyball_chips.ezo_do import EzoDoConfig
from flyball_chips.ezo_ec import EzoEcConfig
from flyball_chips.ezo_orp import EzoOrpConfig
from flyball_chips.ezo_ph import EzoPhConfig
from flyball_chips.htu21d import Htu21dConfig
from flyball_chips.hx711 import Hx711Config
from flyball_chips.mcp3008 import Mcp3008Config
from flyball_chips.mcp4725 import Mcp4725Config
from flyball_chips.mhz19 import MhZ19Config
from flyball_chips.ms5611 import Ms5611Config
from flyball_chips.scd4x import Scd4xConfig
from flyball_chips.scd30 import Scd30Config
from flyball_chips.sgp30 import Sgp30Config
from flyball_chips.sgp40 import Sgp40Config
from flyball_chips.sht4x import Sht4xConfig, Sht4xSetConfig
from flyball_chips.sht31 import Sht31Config


def register(catalog: Catalogs) -> None:
    catalog.register_device(Ads1115Config)
    catalog.register_device(Bme280Config)
    catalog.register_device(Ccs811Config)
    catalog.register_device(EzoDoConfig)
    catalog.register_device(EzoEcConfig)
    catalog.register_device(EzoOrpConfig)
    catalog.register_device(EzoPhConfig)
    catalog.register_device(Htu21dConfig)
    catalog.register_device(Hx711Config)
    catalog.register_device(Mcp3008Config)
    catalog.register_device(Mcp4725Config)
    catalog.register_device(MhZ19Config)
    catalog.register_device(Ms5611Config)
    catalog.register_device(Scd4xConfig)
    catalog.register_device(Scd30Config)
    catalog.register_device(Sgp30Config)
    catalog.register_device(Sgp40Config)
    catalog.register_device(Sht4xConfig)
    catalog.register_device(Sht4xSetConfig)
    catalog.register_device(Sht31Config)
