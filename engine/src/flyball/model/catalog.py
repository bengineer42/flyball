"""Catalog: what's installed, by tag. Explicit registration, no import-side-effect magic.

A [Catalog][flyball.model.catalog.Catalog] holds one kind of thing (devices,
links, laws, ...), tag to class, collision-checked. [Catalogs][flyball.model.catalog.Catalogs]
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
"what's compiled in" path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flyball.foundation.device import DriverConfig
    from flyball.model.config import Config
    from flyball.model.feedforward import Feedforward
    from flyball.model.generator import SetPointGenerator
    from flyball.model.law import ControlLaw


class Catalog[T]:
    """Tag -> class, for one kind of thing. Collision-checked, nothing implicit."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._by_tag: dict[str, type[T]] = {}

    def register(self, cls: type[T], *, tag: str | None = None) -> None:
        """Register `cls` under `tag` (`cls.config_tag`/`cls.tag` if omitted).

        Raises:
            ValueError: `tag` is already registered to a different class. Not
                raised on re-registering the same class under the same tag
                (safe to call `discover()` more than once).
        """
        resolved = tag or getattr(cls, "config_tag", None) or getattr(cls, "tag", None)
        if not resolved:
            raise ValueError(f"{cls.__name__} has no tag; pass one or declare it on the class")
        clash = self._by_tag.get(resolved)
        if clash is not None and clash is not cls:
            raise ValueError(f"{self.kind} tag {resolved!r} is already {clash.__name__}")
        self._by_tag[resolved] = cls

    def __getitem__(self, tag: str) -> type[T]:
        try:
            return self._by_tag[tag]
        except KeyError:
            raise KeyError(f"{self.kind} tag {tag!r} is not registered") from None

    def __contains__(self, tag: str) -> bool:
        return tag in self._by_tag

    def __iter__(self):
        return iter(self._by_tag)

    def __len__(self) -> int:
        return len(self._by_tag)

    def tags(self) -> list[str]:
        return list(self._by_tag)

    def items(self):
        return self._by_tag.items()


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
    generators: Catalog[SetPointGenerator] = field(default_factory=lambda: Catalog("generator"))

    def register_device(self, cls: type[DriverConfig[Any]], *, tag: str | None = None) -> None:
        self.devices.register(cls, tag=tag)

    def register_link(self, cls: type[Config[Any]], *, tag: str | None = None) -> None:
        self.links.register(cls, tag=tag)

    def register_law(self, cls: type[ControlLaw], *, tag: str | None = None) -> None:
        self.laws.register(cls, tag=tag)

    def register_feedforward(self, cls: type[Feedforward], *, tag: str | None = None) -> None:
        self.feedforwards.register(cls, tag=tag)

    def register_generator(self, cls: type[SetPointGenerator], *, tag: str | None = None) -> None:
        self.generators.register(cls, tag=tag)

    def discover(self, group: str = "flyball.configs") -> list[str]:
        """Call every installed package's `register(self)`, by its `flyball.configs` entry point.

        A package declares one in its `pyproject.toml`::

            [project.entry-points."flyball.configs"]
            my_package = "my_package.configs"

        pointing at a module with a `def register(catalog) -> None`. Returns
        the entry names loaded. Safe to call more than once -- registering
        the same class under the same tag twice is not a collision.
        """
        from importlib.metadata import entry_points

        loaded = []
        for entry in entry_points(group=group):
            module = entry.load()
            module.register(self)
            loaded.append(entry.name)
        return loaded
