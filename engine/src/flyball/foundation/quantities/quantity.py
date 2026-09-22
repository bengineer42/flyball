"""What is measured or set, independent of any device: a name and a unit.

A [Quantity][flyball.foundation.quantities.quantity.Quantity] is a plain value: two devices
that both report `temperature` in °C hold equal quantities without sharing
an object, and nothing is interned. Range, precision and bands are the
signal's, since two thermocouples on one rig can differ in all of them.

A `Quantity` can also stand as `Annotated` metadata on a plain float, the
same way [UnitRef][flyball.foundation.quantities.types.UnitRef] does: pydantic puts
`unit`, `dimension` and `quantity` in the field's JSON schema, and
[unit_of][flyball.foundation.quantities.types.unit_of] reads it back in-process. This
is how a driver's typed alias, `Annotated[float, FLOW]`, carries both the
unit and the quantity's name without a separate `UnitRef`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_core import core_schema

from .dimension import Unit


@dataclass(frozen=True, slots=True)
class Quantity:
    """What is measured or set: a name and a unit, nothing else.

    `unit` may be given as a symbol, as a file writes it (`"°C"`).
    """

    name: str
    unit: Unit

    def __init__(self, name: str, unit: Unit | str) -> None:
        if isinstance(unit, str):
            unit = Unit.get(unit)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "unit", unit)

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
        json["quantity"] = self.name
        return json

    def __repr__(self) -> str:
        return f"Quantity({self.name!r}, {self.unit})"
