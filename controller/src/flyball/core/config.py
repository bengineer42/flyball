"""Configs: descriptions that build things, and the tag that tells them apart in a file.

`Config[T]` is a pydantic model with `build() -> T`. Where a field admits
several implementations, each config declares a `tag` and
[Config.union][flyball.core.config.Config.union] gives the discriminated
union to validate against.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Annotated, Any, ClassVar, Literal, Union, overload

from pydantic import BaseModel, Field, create_model


class Config[T](BaseModel, ABC):
    """What to build and how. Subclass with `tag="..."` to make it selectable by name."""

    config_tag: ClassVar[str | None] = None
    """The tag this config is selectable by. The generated tagged model carries it as a field."""
    registry: ClassVar[
        dict[str, type]
    ] = {}  # `type[Config]` here would make pydantic rebuild a half-built class
    """Every tagged config, by tag. One namespace: a tag names one kind of thing."""

    def __init_subclass__(cls, tag: str | None = None, **kwargs: Any) -> None:
        # Python hands class keywords here first; pydantic hands them again to
        # `__pydantic_init_subclass__` once the model is built. Accept here,
        # act there.
        super().__init_subclass__(**kwargs)

    @classmethod
    def __pydantic_init_subclass__(cls, tag: str | None = None, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if tag is None:
            return
        if (clash := Config.registry.get(tag)) is not None and clash is not cls:
            raise ValueError(f"config tag {tag!r} is already {clash.__name__}")
        cls.config_tag = tag
        Config.registry[tag] = cls

    @abstractmethod
    def build(self) -> T: ...

    @classmethod
    def tagged(cls) -> type[Config[Any]]:
        """This config with a `tag` field fixed to its tag, for a discriminated union."""
        if cls.config_tag is None:
            raise TypeError(f"{cls.__name__} has no tag; declare it with `tag=`")
        if "_tagged" not in cls.__dict__:
            model = create_model(  # pyright: ignore[reportCallIssue]
                f"{cls.__name__}Tagged",
                __base__=cls,
                tag=(Literal[cls.config_tag], cls.config_tag),  # pyright: ignore[reportArgumentType]
            )
            cls._tagged = model  # type: ignore[attr-defined]
        return cls._tagged  # type: ignore[attr-defined, no-any-return]

    @classmethod
    def union(cls, *members: type[Config[Any]]) -> Any:
        """`Annotated[A | B | ..., Field(discriminator="tag")]` over the given tagged configs."""
        if not members:
            raise TypeError("say which configs the union admits")
        return Annotated[
            Union[tuple(m.tagged() for m in members)],  # ruff: ignore[non-pep604-annotation-union]  pydantic needs the Union form
            Field(discriminator="tag"),
        ]


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
