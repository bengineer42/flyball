"""Access sets, signal and node specs, and what moves on bound signals."""

from __future__ import annotations

from dataclasses import fields

import pytest

from flyball.core.device import Device
from flyball.core.errors import NotFoundError
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, NodeSpec, Reading, Sample, SignalSpec, WriteState
from flyball.core.units.si import Celsius, Watt

TEMP = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)


class TestAccess:
    def test_flags_and_named_sets(self):
        assert Access.RP == Access.R | Access.P
        assert Access.RW == Access.R | Access.W
        assert Access.RPW == Access.R | Access.P | Access.W
        assert Access.P in Access.RP and Access.W not in Access.RP

    def test_p_implies_r(self):
        for _ in range(2):  # a refused composite is not cached by Flag
            with pytest.raises(ValueError, match="P without R"):
                Access.P | Access.W
            with pytest.raises(ValueError, match="P without R"):
                Access(6)
        with pytest.raises(ValueError, match="P without R"):
            Access.parse("p")
        with pytest.raises(ValueError, match="P without R"):
            Access.check(Access.P)
        with pytest.raises(ValueError, match="P without R"):
            SignalSpec(name="x", quantity=TEMP, access=Access.P)

    def test_parse_and_str_round_trip(self):
        for text in ("r", "rp", "w", "rw", "rpw"):
            assert str(Access.parse(text)) == text
        assert Access.parse("PR") is Access.RP
        assert Access.parse("wr") is Access.RW
        assert str(Access.W) == "w"
        with pytest.raises(ValueError, match="'x' is not one of r, p, w"):
            Access.parse("rx")
        with pytest.raises(ValueError, match="repeats 'r'"):
            Access.parse("rr")


class TestSpecs:
    def test_a_segment_has_no_dots(self):
        with pytest.raises(ValueError, match="not an address segment"):
            SignalSpec(name="dry.humidity", quantity=TEMP, access=Access.RP)
        with pytest.raises(ValueError, match="not an address segment"):
            NodeSpec(name="", children=())

    def test_defaults(self):
        spec = SignalSpec(name="zone1", quantity=TEMP, access=Access.RP)
        assert spec.label == "" and spec.range is None and spec.poll_s is None
        assert spec.limits is None and spec.together == frozenset()
        node = NodeSpec(name="dry", children=(spec,))
        assert node.atomic is False and node.poll_s is None

    def test_only_float_scalars_yet_but_the_type_is_on_the_wire(self):
        spec = SignalSpec(name="zone1", quantity=TEMP, access=Access.RP)
        assert spec.dtype == "float" and spec.shape == ()
        assert {f.name for f in fields(spec)} >= {"dtype", "shape"}, "on the wire, no override"
        with pytest.raises(ValueError, match="only float scalars are supported yet"):
            SignalSpec(name="n", quantity=TEMP, access=Access.RP, dtype="int")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="'zone1': dtype 'float' shape \\(3,\\): only float"):
            SignalSpec(name="zone1", quantity=TEMP, access=Access.RP, shape=(3,))


class Probe(Device):
    TREE = (
        NodeSpec(
            name="dry",
            atomic=True,
            children=(
                SignalSpec(name="humidity", quantity=Quantity("humidity", "%"), access=Access.RP),
                SignalSpec(name="temperature", quantity=TEMP, access=Access.RP),
            ),
        ),
        SignalSpec(name="heater", quantity=POWER, access=Access.W, limits=(0.0, 2500.0)),
    )


def test_sample_readings_carry_the_bound_signals():
    probe = Probe("hum")
    dry = probe.nodes["dry"]
    sample = Sample(dry, 1_000, {"humidity": 4.1, "temperature": 21.9})
    readings = list(sample.readings())
    assert [r.signal.address for r in readings] == ["hum.dry.humidity", "hum.dry.temperature"]
    assert readings[0] == Reading(probe.signals["dry.humidity"], 1_000, 4.1)
    assert readings[0].signal is probe.signals["dry.humidity"]
    assert readings[1].value == 21.9 and readings[1].time_ns == 1_000
    assert sample.node.address == "hum.dry" and sample.node.atomic is True
    assert sample.seconds == 1e-6


def test_a_sample_may_carry_the_subtree_by_dotted_keys():
    probe = Probe("hum")
    sample = Sample(probe.root, 1_000, {"dry.humidity": 4.1, "heater": 0.0})
    readings = list(sample.readings())
    assert readings == [
        Reading(probe.signals["dry.humidity"], 1_000, 4.1),
        Reading(probe.signals["heater"], 1_000, 0.0),
    ]
    assert sample.published() == Sample(probe.root, 1_000, {"dry.humidity": 4.1})
    assert sample.under(probe.nodes["dry"]) == Sample(probe.nodes["dry"], 1_000, {"humidity": 4.1})
    assert sample.under(probe.root) is sample
    only_setting = Sample(probe.root, 1_000, {"heater": 0.0})
    assert only_setting.published() is None
    assert only_setting.under(probe.nodes["dry"]) is None
    on_dry = Sample(probe.nodes["dry"], 2_000, {"humidity": 4.2})
    assert on_dry.published() is on_dry, "the same object when nothing is cut"
    assert on_dry.under(probe.root) == Sample(probe.root, 2_000, {"dry.humidity": 4.2})


def test_find_resolves_a_dotted_path_under_a_node():
    probe = Probe("hum_sensors")
    assert probe.root.find("dry.humidity") is probe.signals["dry.humidity"]
    assert probe.root.find("dry") is probe.nodes["dry"]
    assert probe.nodes["dry"].find("humidity") is probe.signals["dry.humidity"]
    assert probe.root.find("") is probe.root
    with pytest.raises(
        NotFoundError, match="'hum_sensors.nope' not found: no 'nope' under hum_sensors"
    ):
        probe.root.find("nope")
    with pytest.raises(NotFoundError, match="no 'x' under hum_sensors.dry.humidity"):
        probe.root.find("dry.humidity.x")
    with pytest.raises(
        NotFoundError, match="'hum_sensors.dry.nope' not found: no 'nope' under hum_sensors.dry"
    ):
        probe.nodes["dry"].find("nope")


def test_a_reading_names_its_signal_by_address():
    probe = Probe("hum")
    reading = Reading(probe.signals["dry.temperature"], 5, 20.0)
    assert reading.signal.address == "hum.dry.temperature"
    assert reading.signal.unit is Celsius
    assert reading.signal.quantity == TEMP


def test_bound_objects_hash_by_identity():
    a, b = Probe("a"), Probe("b")
    assert a.signals["heater"] != b.signals["heater"]
    assert a.signals["heater"].spec == b.signals["heater"].spec
    assert len({a.signals["heater"], b.signals["heater"]}) == 2
    assert a.nodes["dry"] != b.nodes["dry"]


def test_write_state_is_at_a_limit_when_the_value_sits_on_it():
    heater = Probe("p").signals["heater"]
    assert heater.write_state(1200.0) == WriteState(value=1200.0)
    assert heater.write_state(0.0).at_limit == "low"
    assert heater.write_state(2500.0).at_limit == "high"
    assert Probe("q").signals["dry.humidity"].write_state(5.0).at_limit is None


def test_restrict_only_removes_access():
    humidity = Probe("p").signals["dry.humidity"]
    humidity.restrict(Access.R)
    assert humidity.access is Access.R and humidity.spec.access is Access.RP
    with pytest.raises(ValueError, match="'p.dry.humidity' cannot add access w"):
        humidity.restrict(Access.RW)
    with pytest.raises(ValueError, match="P without R"):
        humidity.restrict(Access(Access.P.value))


def test_override_keeps_the_bound_object():
    probe = Probe("p")
    signal = probe.signals["dry.humidity"]
    signal.override(label="Dry", range=(0.0, 100.0), precision=1)
    assert probe.signals["dry.humidity"] is signal
    assert signal.label == "Dry" and signal.spec.range == (0.0, 100.0)
    assert signal.spec.precision == 1 and signal.name == "humidity"
    with pytest.raises(ValueError, match="device root"):
        probe.root.override(label="x")
