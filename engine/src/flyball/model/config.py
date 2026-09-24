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

from abc import ABC, abstractmethod
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


def import_object(dotted: str) -> Any:
    """`package.module.Name` -> the object. Only from packages already installed.

    How a rig file names a driver class it cannot describe: the wrapped
    instruments' configs call this with their `driver` field.
    """
    import importlib

    module_name, _, attribute = dotted.rpartition(".")
    if not module_name:
        raise ValueError(f"{dotted!r} is not a dotted path to a class")
    return getattr(importlib.import_module(module_name), attribute)


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
