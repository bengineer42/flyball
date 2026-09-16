"""A quantity is a name and a unit, equal by value, never interned."""

from __future__ import annotations

from typing import Annotated

import pytest
from pydantic import TypeAdapter

from flyball.core.quantity import Quantity
from flyball.core.units.errors import UnitNotFoundError
from flyball.core.units.si import Celsius, Kelvin, Litre, Minute


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
