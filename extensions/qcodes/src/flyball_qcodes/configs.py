"""The entry-point target: explicitly registers this package's tagged config."""

from flyball.model.catalog import Catalogs

from ._qcodes import QCoDeSConfig


def register(catalog: Catalogs) -> None:
    catalog.register_device(QCoDeSConfig)
