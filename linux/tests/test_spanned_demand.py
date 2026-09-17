"""The unit/span shape shared by `pwm_channel` and `mcp4725`, tested once here."""

import pytest
from flyball.core.quantity import Quantity
from flyball.core.units import DIMENSIONLESS

from flyball_linux.devices.spanned_demand import (
    from_fraction,
    spanned_signal_spec,
    to_fraction,
    validate_span,
)

BARE = Quantity("drive", DIMENSIONLESS.unit("fraction of full", "of full"))


class TestValidateSpan:
    def test_unit_alone_is_refused(self):
        with pytest.raises(ValueError, match="unit.*span"):
            validate_span("V", None)

    def test_span_alone_is_refused(self):
        with pytest.raises(ValueError, match="unit.*span"):
            validate_span(None, (0.0, 1.0))

    def test_a_falling_span_is_refused(self):
        with pytest.raises(ValueError, match="rising"):
            validate_span("V", (10.0, 0.0))

    def test_neither_is_fine(self):
        validate_span(None, None)  # no raise

    def test_both_is_fine(self):
        validate_span("V", (0.0, 10.0))  # no raise

    def test_prefix_is_prepended(self):
        with pytest.raises(ValueError, match=r"^heater: "):
            validate_span("V", None, prefix="heater: ")


class TestSpannedSignalSpec:
    def test_no_unit_is_a_bare_0_to_1_signal(self):
        spec = spanned_signal_spec("drive", None, None, None, bare=BARE)
        assert spec.limits == (0.0, 1.0) and spec.initial == 0.0 and spec.quantity is BARE

    def test_unit_and_span_map_onto_engineering_units(self):
        spec = spanned_signal_spec("drive", "°C", "temperature", (10.0, 40.0), bare=BARE)
        assert spec.limits == (10.0, 40.0) and spec.initial == 10.0
        assert spec.quantity.name == "temperature" and spec.quantity.symbol == "°C"

    def test_quantity_defaults_to_the_signal_name(self):
        spec = spanned_signal_spec("setpoint", "°C", None, (10.0, 40.0), bare=BARE)
        assert spec.quantity.name == "setpoint"

    def test_signal_name_is_not_hardcoded_to_drive(self):
        spec = spanned_signal_spec("position", None, None, None, bare=BARE)
        assert spec.name == "position"


class TestFractionRoundTrip:
    def test_bare_fraction_is_unchanged(self):
        assert to_fraction(0.3, None) == 0.3
        assert from_fraction(0.3, None) == 0.3

    def test_spanned_value_maps_to_and_from_a_fraction(self):
        span = (10.0, 40.0)
        assert to_fraction(25.0, span) == pytest.approx(0.5)
        assert from_fraction(0.5, span) == pytest.approx(25.0)
        assert from_fraction(to_fraction(22.0, span), span) == pytest.approx(22.0)
