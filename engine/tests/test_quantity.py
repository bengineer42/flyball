"""A quantity is a name and a unit, equal by value, never interned."""

from __future__ import annotations

from typing import Annotated

import pytest
from pydantic import TypeAdapter

from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.errors import UnitNotFoundError
from flyball.foundation.quantities.si import Celsius, Kelvin, Litre, Metre, Minute, Watt


def test_equal_by_value_not_identity():
    a = Quantity("temperature", Celsius)
    b = Quantity("temperature", Celsius)
    assert a == b and a is not b
    assert hash(a) == hash(b)
    assert a != Quantity("temperature", Kelvin)
    assert a != Quantity("setpoint", Celsius)


def test_unit_may_be_a_symbol():
    assert Quantity("temperature", "°C") == Quantity("temperature", Celsius)
    assert Quantity("flow", "L/min").unit.symbol == "L/min"
    with pytest.raises(UnitNotFoundError):
        Quantity("nonsense", "zorb")


def test_is_frozen_and_slotted():
    q = Quantity("temperature", Celsius)
    with pytest.raises(AttributeError):
        q.name = "other"  # type: ignore[misc]
    assert not hasattr(q, "__dict__")
    assert repr(q) == "Quantity('temperature', °C)"


def test_symbol_and_dimension():
    q = Quantity("temperature", Celsius)
    assert q.symbol == "°C"
    assert q.dimension == "Temperature"


def test_stands_as_its_own_annotated_metadata():
    flow = Quantity("flow", Litre / Minute)
    schema = TypeAdapter(Annotated[float, flow]).json_schema()
    assert schema["unit"] == "L/min"
    assert schema["dimension"] == "Volume flow"
    assert schema["quantity"] == "flow"


def test_a_unit_makes_its_quantity_named_after_the_dimension():
    assert Celsius.quantity() == Quantity("temperature", Celsius)
    assert (Litre / Minute).quantity("flow") == Quantity("flow", Litre / Minute)
    assert Watt.quantity().name == "power"
    assert Celsius.quantity("chamber").name == "chamber", "a name given wins"


def test_a_unit_on_an_unnamed_dimension_needs_the_name():
    unnamed = Watt / Metre**5  # nothing is declared on M L⁻³ T⁻³
    assert unnamed.dimension.label == unnamed.dimension.formula()
    with pytest.raises(ValueError, match="unnamed dimension"):
        unnamed.quantity()
    assert unnamed.quantity("oddity").unit is unnamed
