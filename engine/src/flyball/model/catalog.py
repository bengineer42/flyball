"""Catalog: what's installed, by type. Explicit registration, no import-side-effect magic.

A [Catalog][flyball.model.catalog.Catalog] holds one kind of thing (devices,
links, laws, ...), type to class, collision-checked. [Catalogs][flyball.model.catalog.Catalogs]
holds one `Catalog` per kind -- the whole install-scoped surface a rig can be
built from.

Registering used to be a side effect of a module happening to get imported
(`__init_subclass__` writing into a bare `ClassVar` dict). That's fragile:
installing a package is not the same as importing it, and importing is not
the same as registering. Here, a package's own `flyball.configs` entry point
names a module with a `register(catalog)` function that calls `catalog
.register_*(...)` explicitly for everything it provides::

    # my_package/configs.py
    def register(catalog: Catalogs) -> None:
        catalog.register_link(I2cConfig)
        catalog.register_device(Sht4xConfig)

and declares it in `pyproject.toml`::

    [project.entry-points."flyball.configs"]
    my_package = "my_package.configs"

[Catalogs.discover][flyball.model.catalog.Catalogs.discover] walks every
installed package's entry point and calls its `register`, so `Catalogs()`
followed by `discover()` is enough to load everything installed -- engine's
own built-in laws included, through the same mechanism, no special-cased
"what's compiled in" path. One package that fails to import or to register
is logged, left out whole and named in
[discovery_errors][flyball.model.catalog.Catalogs.discovery_errors]; the
rest load, and a rig that names one of its types fails as for any unknown
type.

This replaces `Config`'s old `__init_subclass__`-based auto-registration
(`Config.registry`, removed): registering used to be a side effect of a
module happening to get imported, which is exactly how the
bluesky/qcodes/pymeasure regression happened -- installing a package is not
the same as importing it, and importing is not the same as registering.
`runner.py` builds one `Catalogs`, calls `discover()`, and calls
[set_catalog][flyball.model.catalog.set_catalog]; everything that builds or
validates a rig reads [current_catalog][flyball.model.catalog.current_catalog]
/ [get_catalog][flyball.model.catalog.get_catalog] from here rather than a
bare `ClassVar` dict.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flyball.foundation.device import DriverConfig
    from flyball.model.config import Config
    from flyball.model.feedforward import Feedforward
    from flyball.model.generator import SetpointGenerator
    from flyball.model.law import ControlLaw

log = logging.getLogger(__name__)


class Catalog[T]:
    """Type -> class, for one kind of thing. Collision-checked, nothing implicit."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._by_type: dict[str, type[T]] = {}

    def register(self, cls: type[T], *, name: str | None = None) -> None:
        """Register `cls` under `name`.

        By default its `type_name` (a config), `type` (a law, feedforward or
        generator) or `tag` (a program step).

        Raises:
            ValueError: `name` is already registered to a different class. Not
                raised on re-registering the same class under the same name
                (safe to call `discover()` more than once).
        """
        resolved = (
            name
            or getattr(cls, "type_name", None)
            or getattr(cls, "type", None)
            or getattr(cls, "tag", None)
        )
        if not resolved:
            raise ValueError(f"{cls.__name__} has no type; pass one or declare it on the class")
        clash = self._by_type.get(resolved)
        if clash is not None and clash is not cls:
            raise ValueError(f"{self.kind} type {resolved!r} is already {clash.__name__}")
        self._by_type[resolved] = cls

    def __getitem__(self, name: str) -> type[T]:
        try:
            return self._by_type[name]
        except KeyError:
            raise KeyError(f"{self.kind} type {name!r} is not registered") from None

    def get(self, name: str, default: type[T] | None = None) -> type[T] | None:
        return self._by_type.get(name, default)

    def __contains__(self, name: str) -> bool:
        return name in self._by_type

    def __iter__(self):
        return iter(self._by_type)

    def __len__(self) -> int:
        return len(self._by_type)

    def names(self) -> list[str]:
        return list(self._by_type)

    def items(self):
        return self._by_type.items()

    def unregister(self, name: str) -> None:
        """Drop `name`, if present. For a reloadable source (a `drivers/` directory) only."""
        self._by_type.pop(name, None)


@dataclass
class Catalogs:
    """Every registered kind, install-scoped: one `Catalog` per kind of component.

    Built once per process (same lifetime as the `Rig` it builds), populated
    by [discover][flyball.model.catalog.Catalogs.discover] or by calling the
    `register_*` methods directly (e.g. in a test, against a fresh instance).
    """

    devices: Catalog[DriverConfig[Any]] = field(default_factory=lambda: Catalog("device"))
    links: Catalog[Config[Any]] = field(default_factory=lambda: Catalog("link"))
    laws: Catalog[ControlLaw] = field(default_factory=lambda: Catalog("law"))
    feedforwards: Catalog[Feedforward] = field(default_factory=lambda: Catalog("feedforward"))
    generators: Catalog[SetpointGenerator] = field(default_factory=lambda: Catalog("generator"))
    # `Any`, not `type[Step]`: `Step` lives in `flyball.sequencing`, above
    # `model` in the Layers contract -- even a `TYPE_CHECKING`-only import back
    # down would be a real edge (import-linter reads the AST, guard or not).
    steps: Catalog[Any] = field(default_factory=lambda: Catalog("step"))
    discovery_errors: dict[str, str] = field(default_factory=dict)
    """Entry name -> why [discover][flyball.model.catalog.Catalogs.discover] left it out
    (`"ImportError: ..."`); empty when every installed package registered."""

    def _catalogs(self) -> list[Catalog[Any]]:
        return [v for f in fields(self) if isinstance(v := getattr(self, f.name), Catalog)]

    def register_device(self, cls: type[DriverConfig[Any]], *, name: str | None = None) -> None:
        self.devices.register(cls, name=name)

    def register_link(self, cls: type[Config[Any]], *, name: str | None = None) -> None:
        self.links.register(cls, name=name)

    def register_law(self, cls: type[ControlLaw], *, name: str | None = None) -> None:
        self.laws.register(cls, name=name)

    def register_feedforward(self, cls: type[Feedforward], *, name: str | None = None) -> None:
        self.feedforwards.register(cls, name=name)

    def register_generator(self, cls: type[SetpointGenerator], *, name: str | None = None) -> None:
        self.generators.register(cls, name=name)

    def register_step(self, cls: type[Any], *, name: str | None = None) -> None:
        """`cls` is a `sequencing.step.Step` subclass -- untyped here, see `steps`."""
        self.steps.register(cls, name=name)

    def discover(self, group: str = "flyball.configs") -> list[str]:
        """Call every installed package's `register(self)`, by its `flyball.configs` entry point.

        A package declares one in its `pyproject.toml`::

            [project.entry-points."flyball.configs"]
            my_package = "my_package.configs"

        pointing at a module with a `def register(catalog) -> None`. Returns
        the entry names loaded. Safe to call more than once -- registering
        the same class under the same type twice is not a collision.

        An entry that fails -- its module does not import, has no
        `register`, or `register` raises, a type already registered to
        another class among them -- is logged, and whatever it registered
        before failing is taken back out, so the package is left out whole;
        `discovery_errors` names it and why. The others load: one broken
        package does not take every rig down, and a rig that names one of
        its types fails as for any unknown type.
        """
        from importlib.metadata import entry_points

        loaded = []
        for entry in entry_points(group=group):
            before = [(c, dict(c._by_type)) for c in self._catalogs()]
            try:
                entry.load().register(self)
            except Exception as e:
                for catalog, by_type in before:
                    catalog._by_type = by_type
                self.discovery_errors[entry.name] = f"{type(e).__name__}: {e}"
                log.error(
                    "%s entry point %r (%s) skipped, none of its types are registered: %s",
                    group,
                    entry.name,
                    entry.value,
                    self.discovery_errors[entry.name],
                )
                log.debug("%s entry point %r", group, entry.name, exc_info=True)
                continue
            self.discovery_errors.pop(entry.name, None)
            loaded.append(entry.name)
        return loaded


_catalog: Catalogs | None = None
"""The process's installed `Catalogs`, set once at startup. Module-level, not on `Catalogs`
itself, matching `flyball.interfaces.server.deps`'s existing `current_drivers_dir()` shape for
process-scoped state -- but living here, not in `interfaces/server/deps.py`, because code below
`interfaces` (`runtime.config`, `foundation.device.device`, `runner.py`'s CLI path) needs it too,
and `flyball.model` is the one layer reachable from everywhere without a layering-contract
violation (see `engine/pyproject.toml`'s `Layers` contract comment on why `flyball.model` is left
unordered). `interfaces/server/deps.py` re-exports `current_catalog`/`set_catalog` from here and
adds its own `get_catalog()`/`CatalogDep` for FastAPI, so there is one source of truth, not two.
"""


def set_catalog(catalog: Catalogs | None) -> None:
    """The process-wide `Catalogs`. `runner.py` sets this once, right after `discover()`."""
    global _catalog
    _catalog = catalog


def current_catalog() -> Catalogs | None:
    return _catalog


def get_catalog() -> Catalogs:
    """`current_catalog()`, or raise. For code that cannot build or validate a rig without one."""
    if _catalog is None:
        raise RuntimeError(
            "no Catalogs is set; call flyball.model.catalog.ensure_discovered() first"
            " (flyball-runner sets one at startup)"
        )
    return _catalog


def ensure_discovered(group: str = "flyball.configs") -> Catalogs:
    """`current_catalog()`, or a freshly built and discovered one, set as the current one now.

    Idempotent and non-destructive: never overwrites a `Catalogs` already
    set -- discovering again there would drop anything added since (a
    `drivers/` directory's loose files, `/api/drivers/reload`). For a
    one-shot CLI use (`rig_schema()`, `load_rig_config()` called outside a
    running server) and `runner.serve()`, called by an application that built
    its own rig without going through `runner.main()` first.
    """
    catalog = current_catalog()
    if catalog is None:
        catalog = Catalogs()
        catalog.discover(group)
        set_catalog(catalog)
    return catalog
