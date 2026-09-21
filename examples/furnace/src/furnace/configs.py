"""The entry-point target: explicitly registers this package's tagged config."""

from flyball.model.catalog import Catalogs

from furnace.sim import FurnaceConfig


def register(catalog: Catalogs) -> None:
    catalog.register_link(FurnaceConfig)
