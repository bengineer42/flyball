"""How a method's arguments cross the wire.

Each request model is derived from the callable's signature.
[WIRE_TYPES][flyball.interfaces.server.wire.WIRE_TYPES] substitutes the domain types
that cannot cross as they stand. Touches no registry, so it imports before
any command exists.
"""

from __future__ import annotations

from collections.abc import Callable
from inspect import signature
from typing import Any, get_type_hints

from pydantic import BaseModel, ConfigDict, create_model

from flyball.control import ControlLaw, ControlLawConfig, ControlLawLike
from flyball.control.types import ControlLawView, Tuning
from flyball.interfaces.server.schemas import LawConfig

WIRE_TYPES: dict[Any, Any] = {
    # Durations and rates carry their own wire forms (see `core.clock`);
    # only types that cannot -- a running law -- need a stand-in here.
    # A running law cannot cross the wire, so the tuning unions narrow to a
    # config or the name of a stored one.
    ControlLawLike | str | None: LawConfig | str | None,
    Tuning | ControlLaw | ControlLawConfig | ControlLawView | str | None: LawConfig | str | None,
}
"""Domain annotation -> how it crosses the wire.

A parameter whose annotation is not a key here keeps its own type. Keys are
matched whole, so a union must be written exactly as the command declares it.
"""


def wire_fields(fn: Callable[..., Any], *, skip: int = 0) -> dict[str, Any]:
    """`create_model` fields for `fn`'s parameters, domain types swapped for wire ones.

    Args:
        fn: The callable whose signature defines the fields.
        skip: Leading parameters to drop; 1 for an unbound method's `self`.
    """
    hints = get_type_hints(fn, include_extras=True)  # keep Field(...) and UnitRef metadata
    fields: dict[str, Any] = {}
    for name, parameter in list(signature(fn).parameters.items())[skip:]:
        annotation = hints.get(name, Any)
        fields[name] = (
            WIRE_TYPES.get(annotation, annotation),
            ... if parameter.default is parameter.empty else parameter.default,
        )
    return fields


class ArgumentsBase(BaseModel):
    """A request whose fields are a method's arguments."""

    model_config = ConfigDict(extra="forbid")

    def arguments(self) -> dict[str, Any]:
        """The fields as keyword arguments, nested requests parsed to domain values."""
        return {name: value.parse() if hasattr(value, "parse") else value for name, value in self}


def arguments_model(fn: Callable[..., Any], name: str) -> type[ArgumentsBase]:
    """The pydantic request for calling method `fn`: one field per argument after `self`."""
    return create_model(name, __base__=ArgumentsBase, **wire_fields(fn, skip=1))
