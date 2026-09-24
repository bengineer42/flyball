"""Configs: descriptions that build things, and the `type` that tells them apart in a file.

`Config[T]` is a pydantic model with `build() -> T`. Where a field admits
several implementations, each config declares a `type` and
[Config.union][flyball.model.config.Config.union] gives the discriminated
union to validate against.

Declaring `type=` only sets `type_name` -- it no longer writes into a shared
registry (there is no `Config.registry` any more). What's installed, by type,
is [Catalogs][flyball.model.catalog.Catalogs], explicitly registered; see
that module.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Union, overload

from pydantic import BaseModel, Field, create_model


class Config[T](BaseModel, ABC):
    """What to build and how. Subclass with `type="..."` to make it selectable by name."""

    type_name: ClassVar[str | None] = None
    """The type this config is selectable by. The generated tagged model carries it as a field."""

    def __init_subclass__(cls, type: str | None = None, **kwargs: Any) -> None:
        # Python hands class keywords here first; pydantic hands them again to
        # `__pydantic_init_subclass__` once the model is built. Accept here,
        # act there.
        super().__init_subclass__(**kwargs)

    @classmethod
    def __pydantic_init_subclass__(cls, type: str | None = None, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if type is None:
            return
        cls.type_name = type

    @abstractmethod
    def build(self) -> T: ...

    @classmethod
    def tagged(cls) -> type[Config[Any]]:
        """This config with a `type` field fixed to its type, for a discriminated union."""
        if cls.type_name is None:
            raise TypeError(f"{cls.__name__} has no type; declare it with `type=`")
        if "_tagged" not in cls.__dict__:
            model = create_model(  # pyright: ignore[reportCallIssue]
                f"{cls.__name__}Tagged",
                __base__=cls,
                type=(Literal[cls.type_name], cls.type_name),  # pyright: ignore[reportArgumentType]
            )
            cls._tagged = model  # type: ignore[attr-defined]
        return cls._tagged  # type: ignore[attr-defined, no-any-return]

    @classmethod
    def union(cls, *members: type[Config[Any]]) -> Any:
        """`Annotated[A | B | ..., Field(discriminator="type")]` over the given tagged configs."""
        if not members:
            raise TypeError("say which configs the union admits")
        return Annotated[
            Union[tuple(m.tagged() for m in members)],  # ruff: ignore[non-pep604-annotation-union]  pydantic needs the Union form
            Field(discriminator="type"),
        ]


def discover_paths(group: str) -> list[Path]:
    """Every installed package's registered path in `group`.

    Entries come from a package's own `pyproject.toml`::

        [project.entry-points."flyball.board_dirs"]
        linux = "flyball_linux.boards:board_dir"

    but here each entry loads to a `Path` (package data) rather than a
    module imported for its side effect.
    """
    from importlib.metadata import entry_points

    return [entry.load() for entry in entry_points(group=group)]


INSTRUMENT_PACKAGES_ENV = "FLYBALL_INSTRUMENT_PACKAGES"
"""Machine-level: more packages [import_object][flyball.model.config.import_object] may
take classes from, comma-separated (`my_lab.instruments,qcodes_contrib_drivers`). Set in
the runner's environment, never in a rig file; a class from one must still subclass the
library's base."""

_DOTTED = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)+")


def import_object(dotted: str, *, allowed: Sequence[str], base: str) -> type:
    """`package.module.Name` -> the class, if it lives under `allowed` and subclasses `base`.

    How a rig file names a driver class it cannot describe: the wrapped
    instruments' configs call this with their `instrument` field, when the
    config is validated and again when it is built. Importing a module runs
    it, and the class is then called with the file's arguments, so the path
    is held to the library's own drivers: `allowed` are module prefixes
    (`"pymeasure.instruments"`), plus any in `$FLYBALL_INSTRUMENT_PACKAGES`;
    `base` is the dotted base class every driver there subclasses. Anything
    else -- `os.system`, a function, a class from another package -- is
    refused before (or, for the base check, right after) its module is
    imported.

    Raises:
        ValueError: `dotted` is not under `allowed`, cannot be imported, or
            is not a subclass of `base`; the message says which.
    """
    import importlib
    import os

    extra = [p.strip() for p in os.environ.get(INSTRUMENT_PACKAGES_ENV, "").split(",") if p.strip()]
    prefixes = [p.rstrip(".") for p in (*allowed, *extra)]
    if not _DOTTED.fullmatch(dotted):
        raise ValueError(f"{dotted!r} is not a dotted path to a class")
    if not any(dotted.startswith(p + ".") for p in prefixes):
        raise ValueError(
            f"{dotted!r} is not a class from {', '.join(p + '.' for p in prefixes)}: only those"
            f" packages' instrument classes may be named here (more packages: "
            f"${INSTRUMENT_PACKAGES_ENV} on the machine running the rig)"
        )
    base_module, _, base_name = base.rpartition(".")
    try:
        base_class = getattr(importlib.import_module(base_module), base_name)
    except ImportError as e:
        raise ValueError(
            f"{dotted!r}: cannot import {base_module} ({e}); is its library installed?"
        ) from e
    module_name, _, attribute = dotted.rpartition(".")
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise ValueError(f"{dotted!r}: cannot import {module_name}: {e}") from e
    found = getattr(module, attribute, None)
    if found is None:
        raise ValueError(f"{dotted!r}: {module_name} has no {attribute!r}")
    if not (isinstance(found, type) and issubclass(found, base_class)):
        raise ValueError(f"{dotted!r} is not a subclass of {base}")
    return found


type ConfigOr[T] = Config[T] | T


@overload
def resolve[T](config: Config[T]) -> T: ...
@overload
def resolve[T](config: Config[T] | None) -> T | None: ...
@overload
def resolve[T](config: ConfigOr[T]) -> T: ...
def resolve[T](config: Config[T] | T | None) -> T | None:
    if isinstance(config, Config):
        return config.build()
    return config
