"""Rig-file semantics: YAML booleans, explicit nulls, limits that only narrow, refusals."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from flyball.control.laws import P
from flyball.foundation.device import (
    Committable,
    Demand,
    DeviceEntry,
    DriverConfig,
    LimitNotKnownError,
    LimitsInvertedError,
    Output,
    Sample,
)
from flyball.foundation.device.entry import NamespaceOverride, SignalOverride, _override_signal
from flyball.foundation.files import loads
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Percent, Watt
from flyball.interfaces.server.formats import parse
from flyball.model.law import Transfer
from flyball.runtime.overlay import parse_set

POWER = Quantity("power", Watt)
HUMIDITY = Quantity("humidity", Percent)


class Blender(Committable):
    """A heater with fixed limits, a free demand, and a humidity bounded by two supplies."""

    dry = Output("dry", "Dry supply humidity", HUMIDITY)
    wet = Output("wet", "Wet supply humidity", HUMIDITY)
    heater = Demand("heater", "Heater", POWER, limits=(0.0, 2500.0))
    free = Demand("free", "Unbounded demand", POWER)
    humidity = Demand("humidity", "Target humidity", HUMIDITY, limits=(dry, wet))


class BlenderConfig(DriverConfig[Blender]):
    def build(self, name: str, label: str | None = None) -> Blender:
        return Blender(name, label=label)


@pytest.fixture
def blender_tag(fresh, _catalog) -> str:
    tag = fresh("wf0_blender")

    class Tagged(BlenderConfig, tag=tag):
        pass

    _catalog.register_device(Tagged)
    return tag


@pytest.fixture
def build(blender_tag, fresh, _catalog):
    def _build(signals: dict) -> Blender:
        entry = DeviceEntry.model_validate({"driver": blender_tag, "signals": signals})
        device = entry.build(fresh("blender"), catalogs=_catalog)
        assert isinstance(device, Blender)
        return device

    return _build


def _read(rig, device: Blender, **values: float) -> None:
    signals = device.signals
    rig.on_samples([
        Sample(device.root, rig.clock.now_ns(), {signals[k]: v for k, v in values.items()})
    ])


class TestYamlBooleans:
    @pytest.mark.parametrize("word", ["on", "off", "yes", "no", "On", "OFF", "Yes", "NO"])
    def test_yaml_1_1_words_stay_strings(self, word):
        assert loads(f"on_stop: {word}", ".yaml") == {"on_stop": word}
        assert loads(f"{word}: 1", ".yaml") == {word: 1}, "a key, not True/False"

    def test_the_gpio_case(self):
        doc = loads("signals:\n  on: {label: Lamp}\ncommands: [on, off]\n", ".yaml")
        assert doc == {"signals": {"on": {"label": "Lamp"}}, "commands": ["on", "off"]}

    @pytest.mark.parametrize(
        ("word", "value"),
        [("true", True), ("True", True), ("TRUE", True), ("false", False), ("FALSE", False)],
    )
    def test_true_and_false_are_still_booleans(self, word, value):
        assert loads(f"x: {word}", ".yaml") == {"x": value}

    def test_the_other_yaml_readers_agree(self):
        assert parse("on_stop: off", "yaml") == {"on_stop": "off"}
        assert parse_set("devices.x.config.on_stop=off") == (
            ["devices", "x", "config", "on_stop"],
            "off",
        )
        assert parse_set("a=true")[1] is True

    def test_the_duplicate_key_check_is_kept(self):
        with pytest.raises(ValueError, match="duplicate key 'on'"):
            loads("on: 1\non: 2\n", ".yaml")


class TestExplicitNull:
    def test_a_null_clears_the_field(self, build):
        device = build({"free": {"label": "Fan", "precision": 2, "poll_s": 3}})
        assert device.signals["free"].spec.precision == 2
        device = build({"free": {"label": None, "precision": None, "poll_s": None}})
        free = device.signals["free"]
        assert free.spec.label == "" and free.spec.precision is None and free.spec.poll_s is None

    def test_a_null_clears_a_driver_value(self, build):
        device = build({})
        device.signals["free"].override(warn=(0.0, 10.0), stale_after=5.0)
        _override_signal(device.signals["free"], SignalOverride.model_validate({"warn": None}))
        assert device.signals["free"].spec.warn is None, "explicit null clears"
        assert device.signals["free"].spec.stale_after == 5.0, "absent key leaves alone"

    def test_an_absent_key_leaves_it_alone(self, build):
        device = build({"heater": {"label": "Heater 1"}})
        assert device.signals["heater"].limits == (0.0, 2500.0)
        assert device.signals["heater"].label == "Heater 1"

    def test_a_null_limits_does_not_remove_the_driver_s(self, build):
        heater = build({"heater": {"limits": None}}).signals["heater"]
        assert heater.limits == (0.0, 2500.0)
        assert heater.clamp(9000.0) == 2500.0


class TestLimitsNarrowOnly:
    def test_a_narrower_band_narrows(self, build):
        heater = build({"heater": {"limits": [100, 2000]}}).signals["heater"]
        assert heater.limits == (100.0, 2000.0)
        assert heater.clamp(9000.0) == 2000.0 and heater.clamp(0.0) == 100.0

    @pytest.mark.parametrize("band", [[0, 5000], [-1, 2000], [-10, 9000]])
    def test_a_wider_band_is_refused_at_load(self, build, band):
        with pytest.raises(ValueError, match=r"heater.*outside the driver's"):
            build({"heater": {"limits": band}})

    def test_a_signal_without_driver_limits_takes_the_file_s(self, build):
        free = build({"free": {"limits": [-5, 5]}}).signals["free"]
        assert free.limits == (-5.0, 5.0) and free.clamp(99.0) == 5.0

    def test_a_referenced_bound_is_intersected_live(self, rig, build):
        device = build({"humidity": {"limits": [10, 80]}})
        rig.add_device(device)
        humidity = device.signals["humidity"]
        _read(rig, device, dry=5.0, wet=95.0)
        assert humidity.limits == (10.0, 80.0), "the file's band inside the supplies'"
        assert humidity.clamp(99.0) == 80.0 and humidity.clamp(0.0) == 10.0
        _read(rig, device, dry=20.0, wet=60.0)
        assert humidity.limits == (20.0, 60.0), "the supplies' band inside the file's"
        assert humidity.clamp(99.0) == 60.0 and humidity.clamp(0.0) == 20.0

    def test_a_referenced_bound_with_no_value_still_fails_closed(self, rig, build):
        device = build({"humidity": {"limits": [10, 80]}})
        rig.add_device(device)
        _read(rig, device, dry=5.0)
        with pytest.raises(LimitNotKnownError, match="'wet'"):
            device.signals["humidity"].clamp(50.0)
        _read(rig, device, wet=math.nan)
        with pytest.raises(LimitNotKnownError):
            device.signals["humidity"].clamp(50.0)

    def test_a_rig_demand_is_clamped_to_the_intersection(self, rig, build):
        device = build({"humidity": {"limits": [10, 80]}})
        rig.add_device(device)
        humidity = device.signals["humidity"]
        _read(rig, device, dry=5.0, wet=95.0)
        assert rig.demand(device.root, {humidity: 99.0})[humidity].value == 80.0


class TestInvertedLimits:
    def test_supplies_crossed_raise(self, rig, build):
        device = build({})
        rig.add_device(device)
        _read(rig, device, dry=70.0, wet=30.0)
        with pytest.raises(LimitsInvertedError, match="inverted"):
            device.signals["humidity"].clamp(50.0)

    def test_a_narrowing_disjoint_from_the_live_band_raises(self, rig, build):
        device = build({"humidity": {"limits": [10, 30]}})
        rig.add_device(device)
        _read(rig, device, dry=40.0, wet=90.0)
        with pytest.raises(LimitsInvertedError):
            device.signals["humidity"].clamp(50.0)

    def test_a_command_demand_is_refused_not_clamped(self, rig, build):
        device = build({})
        rig.add_device(device)
        humidity = device.signals["humidity"]
        _read(rig, device, dry=70.0, wet=30.0)
        with pytest.raises(LimitsInvertedError):
            rig.demand(device.root, {humidity: 50.0})
        assert device.written == {}

    def test_a_controller_s_demand_is_held(self, rig, build):
        device = build({})
        rig.add_device(device)
        humidity, dry = device.signals["humidity"], device.signals["dry"]
        controller = rig.attach_controller(humidity, dry, law=P(kp=1.0))
        _read(rig, device, dry=70.0, wet=30.0)
        controller.regulate(50.0, transfer=Transfer.RESET)
        assert device.written == {}, "held, not clamped to either end"
        assert [e.kind for e in rig.recent if e.kind.startswith("limit_")] == ["limit_unknown"]


class TestNamespaceAndDevicePeriods:
    @pytest.mark.parametrize("value", [math.nan, 0.0, -2.0])
    def test_are_refused_too(self, blender_tag, value):
        with pytest.raises(ValidationError, match="poll_s"):
            DeviceEntry.model_validate({"driver": blender_tag, "poll_s": value})
        with pytest.raises(ValidationError, match="poll_s"):
            NamespaceOverride.model_validate({"poll_s": value})


class TestOverridePeriods:
    @pytest.mark.parametrize("field", ["stale_after", "poll_s"])
    @pytest.mark.parametrize("value", [math.nan, math.inf, 0.0, -1.0])
    def test_non_positive_or_non_finite_is_refused(self, field, value):
        with pytest.raises(ValidationError, match=field):
            SignalOverride.model_validate({field: value})

    @pytest.mark.parametrize("field", ["stale_after", "poll_s"])
    def test_a_positive_value_and_null_are_accepted(self, field):
        assert getattr(SignalOverride.model_validate({field: 0.5}), field) == 0.5
        assert getattr(SignalOverride.model_validate({field: None}), field) is None


class TestWaitRouteNamed:
    def test_the_docstrings_name_the_real_route(self):
        import inspect

        from flyball.interfaces.server.routes.waits import router
        from flyball.rig.triggers import Triggers
        from flyball.sequencing.activities import Wait

        assert "/api/waits/{name}/fire" in {route.path for route in router.routes}  # type: ignore[attr-defined]
        for text in (inspect.getdoc(Wait), inspect.getsource(Triggers)):
            assert "/api/signals/" not in (text or "")
            assert "POST /api/waits/{name}/fire" in (text or "")
