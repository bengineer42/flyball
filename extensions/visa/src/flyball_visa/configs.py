"""The entry-point target: explicitly registers every tag this package provides."""

from flyball.model.catalog import Catalogs

from ._links import FakeTextLinkConfig, SerialLinkConfig, VisaLinkConfig
from ._scpi import ScpiConfig


def register(catalog: Catalogs) -> None:
    catalog.register_link(FakeTextLinkConfig)
    catalog.register_link(VisaLinkConfig)
    catalog.register_link(SerialLinkConfig)
    catalog.register_device(ScpiConfig)
