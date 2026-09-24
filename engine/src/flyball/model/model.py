from collections.abc import Callable, Mapping
from inspect import signature
from typing import Annotated, Any, Union, get_type_hints

from pydantic import Field, create_model


def creation_model(
    cls: type,
    name: str | None = None,
    suffix: str | None = "Args",
    base=None,
    extra: dict[str, Any] | None = None,
):
    """The pydantic model for constructing `cls`: one field per constructor parameter.

    Args:
        cls: The class whose `__init__` defines the fields.
        name: Model name stem. Defaults to `cls.__name__`.
        suffix: Appended to the stem, e.g. `"Config"`.
        base: Model to inherit from.
        extra: Fields beyond the constructor's, as `{name: (annotation, default)}`.

    Raises:
        TypeError: If `cls` takes `*args` or `**kwargs`.
    """
    hints = get_type_hints(cls.__init__)
    fields: dict[str, Any] = {}
    for field, parameter in signature(cls).parameters.items():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            raise TypeError(f"{cls.__name__} takes *args/**kwargs; no schema can be derived")
        fields[field] = (
            hints.get(field, Any),
            ... if parameter.default is parameter.empty else parameter.default,
        )
    fields.update(extra or {})
    return create_model((name or cls.__name__) + (suffix or ""), __base__=base, **fields)


class ModelOf:
    """A model on the class, an instance of it on the instance.

    Through the class, the model itself; through an instance, a model
    populated from that instance's `names`.
    """

    def __init__(self, model: type, names: tuple[str, ...]) -> None:
        self._model = model
        self._names = names

    @property
    def model(self) -> type:
        """The model this descriptor hands out."""
        return self._model

    def __get__(self, obj: Any, owner: type | None = None) -> Any:
        if obj is None:
            return self._model
        return self._model(**{name: getattr(obj, name) for name in self._names})


def discriminated_union[T](
    members: Mapping[str, type[T]],
    discriminator: str,
    parser: Callable[[type[T]], type[Any]] = lambda x: x,
) -> Any:
    """One model per registry entry, discriminated by the type field.

    `Annotated[A | B | ..., Field(discriminator=...)]` over `parser(member)`,
    so a union built from a registry admits whatever is registered.

    """
    return Annotated[
        Union[tuple(parser(member) for member in members.values())],  # ruff: ignore[non-pep604-annotation-union]
        Field(discriminator=discriminator),
    ]
