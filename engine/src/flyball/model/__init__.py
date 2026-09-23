"""Catalog -> Config -> Instance, generic: devices, links, laws, feedforwards and generators.

Only the foundation-independent surface is re-exported flat here (`Config`,
`Catalog`/`Catalogs`, the `creation_model`/`ModelOf`/`discriminated_union`
schema machinery): `controller.py`/`law.py`/`feedforward.py`/`generator.py`/
`errors.py` depend on `flyball.foundation` (`Signal`, `Clock`, `Labelled`,
...), and `flyball.foundation.device` in turn builds `DriverConfig` on this
package's own `Config` (`from flyball.model.config import Config`). Eagerly
importing the foundation-dependent submodules here would make `flyball.model`
and `flyball.foundation` initialise each other: `foundation/device/device.py`
imports `flyball.model.config.Config` partway through `foundation/__init__
.py`'s own import, and if that pulled in `model/controller.py` too,
`controller.py`'s `from flyball.foundation import Clock` would hit a
half-built `flyball.foundation` module.

Import `Controller`/`ControllerSpec`/... from `.controller`, `ControlLaw`/
`ControlLawConfig`/... from `.law`, `Feedforward`/... from `.feedforward`,
`SetpointGenerator`/... from `.generator`, the error taxonomy from `.errors`
-- each submodule is safe to import once `flyball.foundation` itself is
past its own import (true for every consumer except `foundation/device
/device.py` itself).
"""

from .catalog import Catalog, Catalogs, current_catalog, get_catalog, set_catalog
from .config import Config, ConfigOr, discover_paths, import_object, resolve
from .model import ModelOf, creation_model, discriminated_union

__all__ = [
    "Catalog",
    "Catalogs",
    "Config",
    "ConfigOr",
    "ModelOf",
    "creation_model",
    "current_catalog",
    "discover_paths",
    "discriminated_union",
    "get_catalog",
    "import_object",
    "resolve",
    "set_catalog",
]
