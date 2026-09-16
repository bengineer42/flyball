"""What is measured or set, independent of any device: a name and a unit.

A [Quantity][flyball.core.quantity.Quantity] is a plain value: two devices
that both report `temperature` in °C hold equal quantities without sharing
an object, and nothing is interned. Range, precision and bands are the
signal's, since two thermocouples on one rig can differ in all of them.
"""

from __future__ import annotations

from dataclasses import dataclass

from .units import Unit


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

    def __repr__(self) -> str:
        return f"Quantity({self.name!r}, {self.unit})"
