from inspect import signature
from typing import Any, get_type_hints

from pydantic import create_model


def creation_model(
    cls: type,
    name: str | None = None,
    suffix: str | None = "Args",
    base=None,
    extra: dict[str, Any] | None = None,
):
    """The pydantic model for constructing ``cls``, taken from its signature.

    One field per constructor parameter, keeping its annotation and default, so
    a class that can be built can also be described, validated and sent over the
    wire without the fields being written twice.

    Args:
        cls: The class whose ``__init__`` defines the fields.
        name: Model name stem. Defaults to ``cls.__name__``.
        suffix: Appended to the stem, e.g. ``"Config"``.
        base: Model to inherit from, for shared behaviour and ``isinstance``.
        extra: Fields to add beyond the constructor's own, as
            ``{name: (annotation, default)}``.

    Returns:
        The generated model.

    Raises:
        TypeError: If ``cls`` takes ``*args`` or ``**kwargs``, which have no
            field names to derive.
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

    Accessed through the owning class the descriptor returns the model itself,
    so its schema is reachable without constructing anything. Accessed through
    an instance it reads ``names`` off that instance and returns a populated
    model.
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
