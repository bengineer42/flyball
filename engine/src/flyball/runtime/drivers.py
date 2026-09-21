"""Drivers from a directory of `.py` files: imported at start, reloadable while the runner runs.

A driver package declares its configs through an entry point and is
installed; a driver being written lives in `drivers/` beside the rig file
and is just imported. Reloading a file re-registers its tags: the old
classes are dropped from the registry first, so a tag does not clash with
its own earlier self. Devices already built on the old class keep it; add
the device again to get the new one.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from flyball.foundation.config import Config

log = logging.getLogger("flyball.drivers")

PACKAGE = "flyball_drivers"
"""The module namespace a directory's files are imported under: `flyball_drivers.<stem>`."""


@dataclass(frozen=True, slots=True)
class DriversReport:
    """What a load registered, file by file, and what failed to import."""

    directory: str
    registered: dict[str, list[str]] = field(default_factory=dict)
    """File stem -> the tags its import registered."""
    errors: dict[str, str] = field(default_factory=dict)
    """File stem -> the import error, one line."""


def load_drivers(directory: str | Path) -> DriversReport:
    """Import (or reload) every `.py` in `directory` and say what each registered.

    A missing directory is an empty report, not an error: `drivers/`
    beside a rig file is optional.
    """
    directory = Path(directory)
    report = DriversReport(str(directory))
    if not directory.is_dir():
        return report
    for path in sorted(directory.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        name = f"{PACKAGE}.{path.stem}"
        _forget(name)
        before = set(Config.registry)
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        except Exception as e:
            sys.modules.pop(name, None)
            _forget(name)
            report.errors[path.stem] = f"{type(e).__name__}: {e}"
            log.warning("driver %s: %s", path, report.errors[path.stem])
            continue
        report.registered[path.stem] = sorted(set(Config.registry) - before)
    return report


def _forget(module: str) -> None:
    """Drop the tags `module` registered, so importing it again does not clash with itself."""
    for tag, config in list(Config.registry.items()):
        if config.__module__ == module:
            del Config.registry[tag]


__all__ = ["PACKAGE", "DriversReport", "load_drivers"]
