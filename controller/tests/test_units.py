"""Dimensions, units, and the wire forms of durations and rates."""

from __future__ import annotations

import pickle

import pytest
from pydantic import TypeAdapter, ValidationError

from flyball.core.clock import Duration, Rate, TimeUnit
from flyball.core.units import dimensions as d
from flyball.core.units.dimension import DIMENSIONLESS, BaseDimension, Dimension, Kilo, Milli
from flyball.core.units.errors import DimensionMismatchError
from flyball.core.units.other import Fahrenheit
from flyball.core.units.si import (
    Celsius,
    Gram,
    Joule,
    Kelvin,
    Kilogram,
    Litre,
    Metre,
    Minute,
    Newton,
    Second,
)


class TestDimension:
    def test_equal_regardless_of_input_order_and_zeros(self):
        assert Dimension([(1, 3), (2, -1)]) == Dimension([(2, -1), (1, 3), (0, 0)])
        assert Dimension([(1, 1), (1, -1)]) == DIMENSIONLESS

    def test_arithmetic_and_str(self):
        flow = d.Length**3 / d.Time
        assert flow == d.VolumeFlow
        assert str(flow) == "L³ T⁻¹"
        assert (flow * d.Time) == d.Volume
        assert str(d.Force) == "M L T⁻²"

    def test_named_equality_is_the_tuple_s(self):
        assert d.Torque == d.Energy
        assert d.Torque.name == "Torque" and d.Energy.name == "Energy"
        assert hash(d.Torque) == hash(d.Energy)

    def test_label_prefers_a_declared_name_and_falls_back_to_the_formula(self):
        assert d.Force.label == "Force"
        assert (d.Energy / d.Time).label == "Power"
        assert (d.Length**5).label == "L⁵"
        assert (d.Energy / d.Time).describe() == "Power (M L² T⁻³)"

    def test_base_dimension_is_a_dimension_and_pickles_to_itself(self):
        assert isinstance(d.Mass, Dimension)
        assert d.Mass == Dimension([(d.Mass.axis, 1)])
        assert pickle.loads(pickle.dumps(d.Mass)) is d.Mass
        assert pickle.loads(pickle.dumps(d.SpecificHeatCapacity)) == d.SpecificHeatCapacity

    def test_duplicate_base_refused(self):
        with pytest.raises(ValueError, match="already registered"):
            BaseDimension("Mass", "M")


class TestUnit:
    def test_kilogram_is_the_coherent_unit_but_gram_takes_prefixes(self):
        assert Kilogram.factor == 1.0
        assert Gram.prefixed(Milli).symbol == "mg"
        assert Gram.prefixed(Milli).factor == pytest.approx(1e-6)
        with pytest.raises(ValueError, match="already carries a prefix"):
            Kilogram.prefixed(Milli)

    def test_composition_tracks_dimension_and_factor(self):
        mlpm = Litre.prefixed(Milli) / Minute
        assert mlpm.dimension == d.VolumeFlow
        assert mlpm.factor == pytest.approx(1e-6 / 60)
        assert (Kilogram * Metre / Second**2).dimension == Newton.dimension

    def test_interval_conversion_ignores_zero_and_absolute_uses_it(self):
        assert Celsius.to(Kelvin, 5) == 5
        assert Celsius.to_absolute(Kelvin, 100) == pytest.approx(373.15)
        assert Fahrenheit.to_absolute(Celsius, 32) == pytest.approx(0.0)
        assert Fahrenheit.to_absolute(Kelvin, 212) == pytest.approx(373.15)

    def test_composition_discards_zero(self):
        assert (Joule / Celsius).zero == 0.0
        assert (Joule / Celsius).factor == (Joule / Kelvin).factor

    def test_mismatch_is_a_typed_error(self):
        with pytest.raises(DimensionMismatchError, match=r"Length \(L\) vs Time \(T\)") as e:
            Metre.to(Second, 1.0)
        assert e.value.source is Metre and e.value.target is Second
        assert isinstance(e.value, ValueError)

    def test_prefix_scales_factor_and_keeps_zero(self):
        kc = Celsius.prefixed(Kilo)
        assert kc.symbol == "k°C" and kc.factor == 1000 and kc.zero == Celsius.zero


class TestDurationAndRateWire:
    adapter = TypeAdapter(Rate | Duration)

    @pytest.mark.parametrize(
        ("wire", "expected"),
        [
            ({"seconds": 90}, Duration(90)),
            ({"minutes": 1, "seconds": 30}, Duration(90)),
            ({"hours": 2}, Duration(7200)),
            (600, Duration(600)),
            ({"per_minute": 2}, Rate(2.0, TimeUnit.MINUTE)),
            ({"per_second": 40}, Rate(40.0, TimeUnit.SECOND)),
            ({"value": 2, "per": "minute"}, Rate(2.0, TimeUnit.MINUTE)),
        ],
    )
    def test_accepts_every_spelling(self, wire, expected):
        assert self.adapter.validate_python(wire) == expected

    @pytest.mark.parametrize(
        "bad", [{"per_fortnight": 1}, {"per_minute": 2, "per_second": 1}, {"weeks": 1}, {}]
    )
    def test_rejects_the_rest(self, bad):
        with pytest.raises(ValidationError):
            self.adapter.validate_python(bad)

    def test_serialises_as_parts(self):
        assert TypeAdapter(Duration).dump_python(Duration(90)) == {"seconds": 90, "nanoseconds": 0}
        assert TypeAdapter(Duration).json_schema()["anyOf"][0]["properties"].keys() >= {
            "seconds",
            "minutes",
        }


class TestUnitLookup:
    @pytest.mark.parametrize(
        ("symbol", "label", "factor"),
        [
            ("°C", "Temperature", 1.0),
            ("mL", "Volume", 1e-6),
            ("kPa", "Pressure", 1000.0),
            ("mL/min", "Volume flow", 1e-6 / 60),
            ("g/m³", "Density", 1e-3),
            ("m/s²", "Acceleration", 1.0),
            ("N·m", "Energy", 1.0),
            ("µL/h", "Volume flow", 1e-9 / 3600),
        ],
    )
    def test_get_resolves_symbols_prefixes_quotients_products_and_powers(
        self, symbol, label, factor
    ):
        from flyball.core.units.dimension import Unit

        unit = Unit.get(symbol)
        assert unit.dimension.label == label and unit.factor == pytest.approx(factor)

    def test_get_returns_the_registered_object_for_an_exact_symbol(self):
        from flyball.core.units.dimension import Unit

        assert Unit.get("°C") is Celsius and Unit.get("K") is Kelvin

    def test_unknown_symbol_is_a_typed_not_found(self):
        from flyball.core.errors import NotFoundError
        from flyball.core.units.dimension import Unit
        from flyball.core.units.errors import UnitNotFoundError

        with pytest.raises(UnitNotFoundError) as e:
            Unit.get("furlong")
        assert isinstance(e.value, NotFoundError)

    def test_a_clashing_definition_of_a_symbol_is_refused(self):
        from flyball.core.units.dimension import Unit
        from flyball.core.units.dimensions import Length, Time

        with pytest.raises(ValueError, match="already"):
            Unit("bogus metre", "m", Time)
        Unit("metre again", "m", Length)  # the same unit under the same symbol is fine


def test_a_measurand_takes_a_unit_by_symbol(fresh):
    from flyball.core.reading import Measurand
    from flyball.core.units import Unit
    from flyball.core.units.errors import UnitNotFoundError

    assert Measurand(fresh("t"), "°C").unit is Unit.get("°C")
    assert Measurand(fresh("p"), "kPa").unit.factor == 1000.0
    with pytest.raises(UnitNotFoundError):
        Measurand(fresh("q"), "furlongs")
