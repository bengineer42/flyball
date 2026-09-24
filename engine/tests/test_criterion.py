"""The criterion: one test on one signal (above, below or near), and what no value means."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from flyball.foundation.device import (
    Criterion,
    Device,
    OnNoValue,
    Reading,
    Readout,
    Reason,
    invalid,
    not_applicable,
    stale,
)
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius


class Probe(Device):
    temperature = Readout("temperature", "Temperature", Quantity("temperature", Celsius))


@pytest.fixture
def reading(fresh):
    signal = Probe(fresh("probe")).signals["temperature"]
    return lambda value: Reading(signal, 0, value)


class TestShape:
    def test_above_below_a_band_or_near(self):
        Criterion(signal="a.b", above=1)
        Criterion(signal="a.b", below=1)
        Criterion(signal="a.b", above=1, below=2)
        Criterion(signal="a.b", near=1, within=0.5)
        with pytest.raises(ValidationError, match="found none"):
            Criterion(signal="a.b")
        with pytest.raises(ValidationError, match=r"found \['above', 'near'\]"):
            Criterion(signal="a.b", above=1, near=2, within=1)
        with pytest.raises(ValidationError, match="above 2.0 is not below 1.0"):
            Criterion(signal="a.b", above=2, below=1)
        with pytest.raises(ValidationError, match="is not below"):
            Criterion(signal="a.b", above=1, below=1)

    def test_within_goes_with_near_and_near_needs_it(self):
        with pytest.raises(ValidationError, match="`near` needs `within`"):
            Criterion(signal="a.b", near=1)
        with pytest.raises(ValidationError, match="`within` goes with `near`"):
            Criterion(signal="a.b", above=1, within=1)
        with pytest.raises(ValidationError, match="negative"):
            Criterion(signal="a.b", near=1, within=-1)

    def test_numbers_are_finite_and_unknown_keys_refused(self):
        with pytest.raises(ValidationError, match="not finite"):
            Criterion(signal="a.b", above=float("inf"))
        with pytest.raises(ValidationError, match="not finite"):
            Criterion(signal="a.b", near=float("nan"), within=1)
        with pytest.raises(ValidationError, match="extra"):
            Criterion.model_validate({"signal": "a.b", "above": 1, "count": 3})

    def test_it_is_written_as_a_rule_would_write_it(self):
        criterion = Criterion.model_validate({
            "signal": "chamber.rh",
            "above": 60,
            "on_no_value": "fire",
        })
        assert criterion.on_no_value is OnNoValue.FIRE and criterion.from_start is False
        assert criterion.describe() == "chamber.rh above 60"
        assert Criterion(signal="s.t", below=60).describe() == "s.t below 60"
        assert Criterion(signal="s.t", near=700, within=5).describe() == "s.t within 5 of 700"
        relative = Criterion(signal="scale.mass", above=36, from_start=True)
        assert relative.describe() == "scale.mass above its start +36"
        assert Criterion(signal="v.l", below=-5, from_start=True).describe() == (
            "v.l below its start -5"
        )


class TestPasses:
    def test_above_and_below_are_strict(self):
        above, below = Criterion(signal="a.b", above=60), Criterion(signal="a.b", below=60)
        assert above.passes(60.1) and not above.passes(60) and not above.passes(59)
        assert below.passes(59.9) and not below.passes(60) and not below.passes(61)

    def test_a_band_is_strict_at_both_ends(self):
        band = Criterion(signal="a.b", above=20, below=80)
        assert band.passes(50) and band.passes(20.1) and band.passes(79.9)
        assert not band.passes(20) and not band.passes(80) and not band.passes(90)
        assert band.describe() == "a.b between 20 and 80"

    def test_near_is_inclusive_either_side(self):
        near = Criterion(signal="a.b", near=700, within=5)
        assert near.passes(695) and near.passes(705) and near.passes(700)
        assert not near.passes(694.9) and not near.passes(705.1)

    def test_from_start_adds_the_start_value(self):
        more = Criterion(signal="scale.mass", above=36, from_start=True)
        assert not more.passes(40, base=10) and more.passes(46.5, base=10)
        near = Criterion(signal="a.b", near=0, within=1, from_start=True)
        assert near.passes(20.5, base=20) and not near.passes(22, base=20)
        assert Criterion(signal="a.b", above=36).passes(40, base=10), "base ignored without it"


class TestMet:
    def test_a_number_is_judged(self, reading):
        criterion = Criterion(signal="a.b", above=60)
        assert criterion.met(reading(61.0)) and not criterion.met(reading(59.0))
        assert criterion.met(reading(True)) is False and Criterion(signal="a.b", above=0.5).met(
            reading(True)
        ), "a boolean line is a number"

    def test_nothing_read_yet_never_meets_it(self):
        assert Criterion(signal="a.b", above=0).met(None, default=OnNoValue.FIRE) is False

    @pytest.mark.parametrize(
        "no_value", [invalid("crc"), stale(Reason.DEVICE_OFFLINE), stale(Reason.SILENT)]
    )
    def test_a_fault_meets_it_only_under_fire(self, reading, no_value):
        ignore = Criterion(signal="a.b", below=60)
        assert ignore.met(reading(no_value)) is False, "settle's default: not met"
        assert ignore.met(reading(no_value), default=OnNoValue.FIRE), "a trip's default: met"
        fire = Criterion(signal="a.b", below=60, on_no_value=OnNoValue.FIRE)
        assert fire.met(reading(no_value)), "its own on_no_value overrides the holder's default"
        never = Criterion(signal="a.b", below=60, on_no_value=OnNoValue.IGNORE)
        assert never.met(reading(no_value), default=OnNoValue.FIRE) is False

    def test_not_applicable_never_meets_it(self, reading):
        fire = Criterion(signal="a.b", below=60, on_no_value=OnNoValue.FIRE)
        assert fire.met(reading(not_applicable("no_flow"))) is False

    def test_a_value_that_is_not_a_number_never_meets_it(self, reading):
        assert Criterion(signal="a.b", above=0).met(reading("auto")) is False
