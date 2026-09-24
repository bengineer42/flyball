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

[describe][flyball.runtime.drivers.describe] is what `GET /api/drivers` lists for each
registered type: its schema, a one-line summary, a link's family, and what a driver
needs before it can be added (the links it takes, its inputs).
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, TypeAliasType, get_args

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


def describe(config: type[Config[Any]], catalogs: Catalogs) -> dict[str, Any]:
    """One registered type as `GET /api/drivers` lists it.

    Every entry has its `role` (`driver` or `link`), `module`, `description` (its own
    docstring, None without one), `summary` (the docstring's first line) and `schema` (or
    `schema_error`, when pydantic cannot build one). A link adds its `family`. A driver adds
    `requires` ([requires][flyball.runtime.drivers.requires]), plus `category` when it
    declares one and `addresses`, the I²C address it defaults to, when its family is `i2c`.
    """
    # Its own docstring: `inspect.getdoc` would give a config that has none `Config`'s.
    description = inspect.cleandoc(config.__doc__) if config.__doc__ else None
    entry: dict[str, Any] = {
        "role": "driver" if issubclass(config, DriverConfig) else "link",
        "module": config.__module__,
        "description": description,
        "summary": description.splitlines()[0] if description else None,
    }
    if issubclass(config, DriverConfig):
        entry["requires"] = requires(config, catalogs)
        if config.category is not None:
            entry["category"] = config.category
        if (
            entry["requires"]["family"] == "i2c"
            and (address := _default_address(config)) is not None
        ):
            entry["addresses"] = [address]
    else:
        entry["family"] = config.family
    try:
        entry["schema"] = config.model_json_schema()
    except Exception as e:  # a schema pydantic cannot build: say so, keep the rest
        entry["schema_error"] = f"{type(e).__name__}: {e}"
    return entry


def requires(driver: type[DriverConfig[Any]], catalogs: Catalogs) -> dict[str, Any]:
    """What `driver` needs before it can be added: `{link, family, inputs}`.

    `family` is the driver's own [family][flyball.model.config.Config.family], else the one
    the configs its `link` field names share (None when it names none, or they disagree).
    `link` is every registered link type of that family, fakes and simulations included, and
    any other its `link` field names; empty for a driver that takes no link. `inputs` are its
    device's declared inputs, each `{role, label, kind, quantity, unit}`: `role` is the name
    under `inputs:`, and `kind` is `signal` (an address to follow, or a number held as a
    constant).
    """
    field = driver.model_fields.get("link")
    named = [] if field is None else _configs_in(field.annotation)
    families = {config.family for config in named if config.family is not None}
    family = driver.family or (families.pop() if len(families) == 1 else None)
    tags = {config.type_name for config in named}
    link = sorted(
        tag
        for tag, config in catalogs.links.items()
        if tag in tags or (family is not None and config.family == family)
    )
    device = driver.device_class()
    inputs = [
        {
            "role": role,
            "label": declared.label,
            "kind": "signal",
            "quantity": declared.quantity.name,
            "unit": declared.quantity.unit.symbol,
        }
        for role, declared in ({} if device is None else device.INPUTS).items()
    ]
    return {"link": link, "family": family, "inputs": inputs}


def _configs_in(annotation: Any) -> list[type[Config[Any]]]:
    """The typed configs an annotation admits: `FakeI2cConfig` in `I2cLinkConfig | str`."""
    if isinstance(annotation, TypeAliasType):
        return _configs_in(annotation.__value__)
    if isinstance(annotation, type):
        return [annotation] if issubclass(annotation, Config) and annotation.type_name else []
    return [config for arg in get_args(annotation) for config in _configs_in(arg)]


def _default_address(driver: type[DriverConfig[Any]]) -> int | None:
    """The default of the driver's `address` field, when it has one."""
    field = driver.model_fields.get("address")
    default = None if field is None else field.default
    return default if isinstance(default, int) and not isinstance(default, bool) else None


__all__ = ["PACKAGE", "DriversReport", "describe", "load_drivers", "requires"]
