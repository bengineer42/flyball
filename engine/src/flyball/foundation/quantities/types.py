"""Annotating a float with the unit it is reported in.

`Measured(Celsius)` is a `float` plus a
[UnitRef][flyball.foundation.quantities.types.UnitRef] in its `Annotated` metadata.
Pydantic puts the unit in the JSON schema;
[unit_of][flyball.foundation.quantities.types.unit_of] reads it back in-process. Nothing
here converts: a driver reports in exactly the annotated unit.

A bare unit with no quantity (a config field like `frequency_hz`) still
wants `UnitRef`; a driver's typed alias for a named quantity is shorter as
`Annotated[float, FLOW]`, a [Quantity][flyball.foundation.quantities.quantity.Quantity]
standing directly as its own metadata -- see `Quantity`'s own pydantic
hook. `unit_of` finds either.
"""

from typing import Annotated, Any, get_type_hints

from pydantic import Field
from pydantic_core import core_schema

from .dimension import Unit


class UnitRef:
    """Annotated metadata: the unit a float is reported in.

    Pydantic puts it in the field's JSON schema;
    `get_type_hints(cls, include_extras=True)` finds the object in-process.
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


def Measured(unit: Unit, /, **constraints: Any) -> Any:
    """A `float` annotated with the unit it is reported in.

    Keyword arguments are pydantic `Field` constraints:
    `Measured(Litre / Minute, ge=0)` also puts `minimum: 0` in the schema.
    Returns `Any`, so it defeats a type checker; prefer the typed form,
    `Annotated[float, QUANTITY]` with a `Quantity`, where one applies.
    """
    return Annotated[float, UnitRef(unit), Field(**constraints)]


def unit_of(cls: type, field: str) -> UnitRef | None:
    """The unit annotating `cls.field` -- a `UnitRef` or a `Quantity` -- or None."""
    from flyball.foundation.quantities import Quantity  # deferred: quantity imports this package

    hint = get_type_hints(cls, include_extras=True)[field]
    for meta in getattr(hint, "__metadata__", ()):
        if isinstance(meta, UnitRef):
            return meta
        if isinstance(meta, Quantity):
            return UnitRef(meta.unit)
    return None
