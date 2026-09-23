"""Drivers from a directory of `.py` files: imported at start, reloadable while the runner runs.

A driver package declares its configs through an entry point and calls
`catalog.register_*(...)` explicitly (`flyball.model.catalog.Catalogs.discover`);
a driver being written lives in `drivers/` beside the rig file instead, with
no `register()` of its own to call -- there is nothing to import it as an
installed package. Reloading a file registers every typed `Config` subclass
it defines, explicitly, into the given `Catalogs` (default: the process's
[current_catalog][flyball.model.catalog.get_catalog]): the old classes are
unregistered first, so a type does not clash with its own earlier self.
Devices already built on the old class keep it; add the device again to get
the new one.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from flyball.foundation.config import Config
from flyball.foundation.device import DriverConfig
from flyball.model.catalog import Catalog, Catalogs, get_catalog

log = logging.getLogger("flyball.drivers")

PACKAGE = "flyball_drivers"
"""The module namespace a directory's files are imported under: `flyball_drivers.<stem>`."""


@dataclass(frozen=True, slots=True)
class DriversReport:
    """What a load registered, file by file, and what failed to import."""

    directory: str
    registered: dict[str, list[str]] = field(default_factory=dict)
    """File stem -> the types its import registered."""
    errors: dict[str, str] = field(default_factory=dict)
    """File stem -> the import error, one line."""


def load_drivers(directory: str | Path, catalogs: Catalogs | None = None) -> DriversReport:
    """Import (or reload) every `.py` in `directory` and say what each registered.

    A missing directory is an empty report, not an error: `drivers/`
    beside a rig file is optional.

    Args:
        directory: Where the `.py` files live.
        catalogs: Where a file's typed configs are registered. Default:
            [get_catalog][flyball.model.catalog.get_catalog].
    """
    catalogs = catalogs or get_catalog()
    directory = Path(directory)
    report = DriversReport(str(directory))
    if not directory.is_dir():
        return report
    for path in sorted(directory.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        name = f"{PACKAGE}.{path.stem}"
        _forget(name, catalogs)
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        except Exception as e:
            sys.modules.pop(name, None)
            _forget(name, catalogs)
            report.errors[path.stem] = f"{type(e).__name__}: {e}"
            log.warning("driver %s: %s", path, report.errors[path.stem])
            continue
        report.registered[path.stem] = _register_module(module, name, catalogs)
    return report


def _register_module(module: ModuleType, name: str, catalogs: Catalogs) -> list[str]:
    """Register every typed `Config` subclass `module` (freshly imported as `name`) defines."""
    added = []
    for value in vars(module).values():
        if not (isinstance(value, type) and issubclass(value, Config)):
            continue
        if value.__module__ != name or value.type_name is None:
            continue
        if issubclass(value, DriverConfig):
            catalogs.register_device(value)
        else:
            catalogs.register_link(value)
        added.append(value.type_name)
    return sorted(added)


def _forget(module: str, catalogs: Catalogs) -> None:
    """Drop the types `module` registered, so importing it again does not clash with itself."""
    for catalog in (catalogs.devices, catalogs.links):
        _forget_from(catalog, module)


def _forget_from(catalog: Catalog[Any], module: str) -> None:
    for name, config in list(catalog.items()):
        if config.__module__ == module:
            catalog.unregister(name)


__all__ = ["PACKAGE", "DriversReport", "load_drivers"]
