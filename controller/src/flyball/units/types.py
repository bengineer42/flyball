"""Annotating a float with the unit it is reported in.

``Quantity(Celsius)`` is a ``float`` to a type checker and to pydantic, plus a
:class:`UnitRef` in its ``Annotated`` metadata. Pydantic reads that while
building the field's JSON schema, so the unit lands on the wire; in-process
code reads it back off the type with :func:`unit_of`. Nothing here converts:
the annotation records what a value *is in*, and a driver reports in exactly
that.
"""

from typing import Annotated, Any, get_type_hints

from pydantic import Field
from pydantic_core import core_schema

from .dimension import Unit


class UnitRef:
    """Annotated metadata: the unit a float is reported in.

    Sits in ``Annotated[float, UnitRef(...)]``. Pydantic calls
    ``__get_pydantic_json_schema__`` while building the field's schema, so the
    unit lands in the JSON; ``get_type_hints(cls, include_extras=True)`` finds
    the object itself, so in-process code (the CLI) never parses JSON to learn it.
    """

    __slots__ = ("unit",)

    def __init__(self, unit: Unit) -> None:
        self.unit = unit

    @property
    def symbol(self) -> str:
        return self.unit.symbol

    @property
    def dimension(self) -> str:
        """The dimension's declared name, or its base-dimension form if unnamed."""
        return self.unit.dimension.label

    def __get_pydantic_json_schema__(
        self, schema: core_schema.CoreSchema, handler: Any
    ) -> dict[str, Any]:
        json = handler(schema)
        json["unit"] = self.symbol
        json["dimension"] = self.dimension
        return json

    def __repr__(self) -> str:
        return f"UnitRef({self.unit.name!r}, {self.symbol!r})"


def Quantity(unit: Unit, /, **constraints: Any) -> Any:
    """A ``float`` annotated with the unit it is reported in.

    ``Quantity(Kelvin)`` reports in K, ``Quantity(Celsius)`` in °C, ``Quantity(Litre / Minute)`` in
    L/min. Keyword arguments are pydantic ``Field`` constraints, so
    ``Quantity(Litre / Minute, ge=0)`` also puts ``minimum: 0`` in the schema.

    Returns:
        ``Annotated[float, UnitRef, Field]`` -- to a type checker it is ``float``.
    """
    return Annotated[float, UnitRef(unit), Field(**constraints)]


def unit_of(cls: type, field: str) -> UnitRef | None:
    """The ``UnitRef`` on ``cls.field``, or None if the field carries no unit."""
    hint = get_type_hints(cls, include_extras=True)[field]
    for meta in getattr(hint, "__metadata__", ()):
        if isinstance(meta, UnitRef):
            return meta
    return None
