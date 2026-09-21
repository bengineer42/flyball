"""Access sets, signal and node specs, and what moves on bound signals."""

from __future__ import annotations

from dataclasses import fields
from enum import Enum

import pytest

from flyball.foundation.device import (
    Access,
    Device,
    NodeSpec,
    Path,
    Reading,
    Sample,
    SignalSpec,
    WriteState,
)
from flyball.foundation.errors import NotFoundError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Watt

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
        assert spec.limits is None
        node = NodeSpec(name="dry", children=(spec,))
        assert node.atomic is False and node.poll_s is None

    def test_typed_scalars_and_the_type_is_on_the_wire(self):
        spec = SignalSpec(name="zone1", quantity=TEMP, access=Access.RP)
        assert spec.vtype is float and spec.dtype == "float" and spec.shape == ()
        assert {f.name for f in fields(spec)} >= {"vtype", "shape"}, "on the wire, no override"

        class Mode(Enum):
            A = "a"

        for vtype, dtype in (
            (int, "int"),
            (bool, "bool"),
            (str, "str"),
            (Mode, "enum"),
            (list[int], "json"),
        ):
            assert SignalSpec(name="n", quantity=TEMP, access=Access.RP, vtype=vtype).dtype == dtype
        with pytest.raises(ValueError, match="'zone1': shape \\(3,\\): only scalars yet"):
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
    humidity, temperature = probe.signals["dry.humidity"], probe.signals["dry.temperature"]
    sample = Sample(dry, 1_000, {humidity: 4.1, temperature: 21.9})
    readings = list(sample.readings())
    assert [r.signal.address for r in readings] == ["hum.dry.humidity", "hum.dry.temperature"]
    assert readings[0] == Reading(humidity, 1_000, 4.1)
    assert readings[0].signal is humidity
    assert readings[1].value == 21.9 and readings[1].time_ns == 1_000
    assert sample.node.address == "hum.dry" and sample.node.atomic is True
    assert sample.seconds == 1e-6


def test_a_sample_may_carry_the_subtree_and_is_cut_without_rekeying():
    probe = Probe("hum")
    dry = probe.nodes["dry"]
    humidity, heater = probe.signals["dry.humidity"], probe.signals["heater"]
    sample = Sample(probe.root, 1_000, {humidity: 4.1, heater: 0.0})
    assert list(sample.readings()) == [
        Reading(humidity, 1_000, 4.1),
        Reading(heater, 1_000, 0.0),
    ]
    assert sample.published() == Sample(probe.root, 1_000, {humidity: 4.1})
    assert sample.under(dry) == Sample(dry, 1_000, {humidity: 4.1})
    assert sample.under(probe.root) is sample
    only_setting = Sample(probe.root, 1_000, {heater: 0.0})
    assert only_setting.published() is None
    assert only_setting.under(dry) is None
    on_dry = Sample(dry, 2_000, {humidity: 4.2})
    assert on_dry.published() is on_dry, "the same object when nothing is cut"
    assert on_dry.under(probe.root) == Sample(probe.root, 2_000, {humidity: 4.2})
    assert next(iter(on_dry.under(probe.root).values)) is humidity, "the same key, whichever node"


def test_by_name_is_the_wire_form_relative_to_a_node():
    probe = Probe("hum")
    dry = probe.nodes["dry"]
    humidity, temperature = probe.signals["dry.humidity"], probe.signals["dry.temperature"]
    sample = Sample(probe.root, 1_000, {humidity: 4.1, probe.signals["heater"]: 0.0})
    assert sample.by_name() == {"dry.humidity": 4.1, "heater": 0.0}
    on_dry = Sample(dry, 2_000, {humidity: 4.2, temperature: 21.9})
    assert on_dry.by_name() == {"humidity": 4.2, "temperature": 21.9}
    assert on_dry.by_name(probe.root) == {"dry.humidity": 4.2, "dry.temperature": 21.9}
    assert on_dry.by_name(dry) == on_dry.by_name()
    with pytest.raises(ValueError, match="'hum.heater' is not under 'hum.dry'"):
        sample.by_name(dry)


def test_contains_and_relative_walk_the_tree():
    probe, other = Probe("hum"), Probe("other")
    dry, humidity, heater = (
        probe.nodes["dry"],
        probe.signals["dry.humidity"],
        probe.signals["heater"],
    )
    assert probe.root.contains(probe.root) and probe.root.contains(dry)
    assert probe.root.contains(humidity) and probe.root.contains(heater)
    assert dry.contains(dry) and dry.contains(humidity)
    assert not dry.contains(heater) and not dry.contains(probe.root)
    assert not probe.root.contains(other.signals["dry.humidity"]), "same tree shape, other device"
    assert not probe.root.contains(other.nodes["dry"])
    assert probe.root.relative(humidity) == "dry.humidity" and dry.relative(humidity) == "humidity"
    assert probe.root.relative(heater) == "heater"
    with pytest.raises(ValueError, match="'hum.heater' is not under 'hum.dry'"):
        dry.relative(heater)
    with pytest.raises(ValueError, match="'other.dry.humidity' is not under 'hum'"):
        probe.root.relative(other.signals["dry.humidity"])


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


class TestPath:
    def test_parse_str_and_join(self):
        path = Path.parse("dry.humidity")
        assert path == ("dry", "humidity") and str(path) == "dry.humidity"
        assert repr(path) == "Path('dry.humidity')"
        assert Path() == () and str(Path()) == "" and Path.parse("") == Path()
        assert Path() / "dry" == Path.parse("dry")
        assert Path.parse("dry") / "humidity" == path
        assert hash(path) == hash(("dry", "humidity")) and {path: 1}[Path.parse("dry.humidity")]

    def test_parent_name_and_is_under(self):
        path = Path.parse("left.dry.humidity")
        assert path.name == "humidity" and path.parent == Path.parse("left.dry")
        assert Path().name == "" and Path().parent == Path()
        assert path.is_under(Path()) and path.is_under(Path.parse("left"))
        assert path.is_under(path) and not path.is_under(Path.parse("left.wet"))
        assert not Path.parse("left").is_under(path)

    def test_an_empty_segment_or_a_dot_is_refused(self):
        for text in (".", "dry.", ".dry", "dry..humidity"):
            with pytest.raises(ValueError, match="an empty segment"):
                Path.parse(text)
        with pytest.raises(ValueError, match="not an address segment"):
            Path() / "dry.humidity"
        with pytest.raises(ValueError, match="not an address segment"):
            Path() / ""

    def test_bound_objects_carry_paths_made_once(self):
        probe = Probe("hum")
        dry, humidity = probe.nodes["dry"], probe.signals["dry.humidity"]
        assert probe.root.path == Path() and dry.path == Path.parse("dry")
        assert humidity.path == Path.parse("dry.humidity")
        assert humidity.path is probe.root.find("dry.humidity").path, "the same object"
        assert humidity.address == "hum.dry.humidity" and dry.address == "hum.dry"
        assert list(probe.signals) == ["conditions", "heater", "dry.humidity", "dry.temperature"]
        assert list(probe.nodes) == ["dry"], "keyed by str(path) for the boundary"


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


def test_ceiling_lets_restrict_widen_up_to_it_not_past_it():
    spec = SignalSpec(name="steps", quantity=TEMP, access=Access.R, ceiling=Access.RP)
    node = NodeSpec(name="stepper", children=(spec,))

    class Widenable(Device):
        TREE = (node,)

    signal = Widenable("s").signals["stepper.steps"]
    assert signal.access is Access.R
    signal.restrict(Access.RP)
    assert signal.access is Access.RP and signal.spec.access is Access.R
    with pytest.raises(ValueError, match="'s.stepper.steps' cannot add access w"):
        signal.restrict(Access.RPW)


def test_ceiling_must_cover_the_declared_access():
    with pytest.raises(ValueError, match="ceiling r excludes p, part of its own declared access"):
        SignalSpec(name="x", quantity=TEMP, access=Access.RP, ceiling=Access.R)


def test_without_a_ceiling_restrict_cannot_widen_at_all():
    heater = Probe("p").signals["heater"]
    assert heater.spec.ceiling is None
    with pytest.raises(ValueError, match="'p.heater' cannot add access r"):
        heater.restrict(Access.RW)


def test_override_keeps_the_bound_object():
    probe = Probe("p")
    signal = probe.signals["dry.humidity"]
    signal.override(label="Dry", range=(0.0, 100.0), precision=1)
    assert probe.signals["dry.humidity"] is signal
    assert signal.label == "Dry" and signal.spec.range == (0.0, 100.0)
    assert signal.spec.precision == 1 and signal.name == "humidity"
    with pytest.raises(ValueError, match="device root"):
        probe.root.override(label="x")
