"""A device's signal tree: binding, views, the write side, and the rig-file envelope."""

from __future__ import annotations

from collections.abc import Iterator, Mapping

import pytest
from pydantic import ValidationError

from flyball.core.config import Config
from flyball.core.device import Device, DeviceEntry, DriverConfig
from flyball.core.quantity import Quantity
from flyball.core.signal import (
    Access,
    NodeSpec,
    Reading,
    Sample,
    Signal,
    SignalSpec,
    WriteState,
)
from flyball.core.units.si import Celsius, Percent, Watt

TEMP = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)
HUMIDITY = Quantity("humidity", Percent)
FLOW = Quantity("flow", "L/min")


def _sensor(name: str) -> NodeSpec:
    return NodeSpec(
        name=name,
        atomic=True,
        children=(
            SignalSpec(name="humidity", quantity=HUMIDITY, access=Access.RP, range=(0.0, 100.0)),
            SignalSpec(name="temperature", quantity=TEMP, access=Access.RP),
        ),
    )


class HumSensors(Device):
    """Three SHT4x namespaces on one device: a static tree."""

    TREE = (_sensor("chamber"), _sensor("dry"), _sensor("wet"))


class SimFurnace(Device):
    """A tree that depends on the config, bound in `__init__`; the base write side."""

    def __init__(
        self, name: str, zones: int, power_w: tuple[float, ...], label: str | None = None
    ) -> None:
        super().__init__(name, label)
        self.zones = zones
        self.inputs: dict[str, float] = {}
        tree: list[NodeSpec | SignalSpec] = [
            SignalSpec(name=f"zone{i}", quantity=TEMP, access=Access.RP, range=(0.0, 1200.0))
            for i in range(1, zones + 1)
        ]
        tree.append(SignalSpec(name="sample", quantity=TEMP, access=Access.RP))
        tree += [
            SignalSpec(
                name=f"heater{i}", quantity=POWER, access=Access.W, limits=(0.0, power_w[i - 1])
            )
            for i in range(1, zones + 1)
        ]
        self.bind(tree)

    def read(self, time_ns: int, node=None) -> Iterator[Sample]:
        yield Sample(self.root, time_ns, dict.fromkeys(self.publishing, 20.0))

    def write_signal(self, signal: Signal, value: float) -> None:
        assert signal.limits is not None
        self.inputs[signal.name] = value / signal.limits[1]


class Blender(Device):
    """A composite actuator: every pending value and every bound input meet in `commit`."""

    TREE = (
        SignalSpec(name="humidity", quantity=HUMIDITY, access=Access.W, limits=(0.0, 100.0)),
        SignalSpec(
            name="dry_flow", quantity=FLOW, access=Access.W, together=frozenset({"wet_flow"})
        ),
        SignalSpec(
            name="wet_flow", quantity=FLOW, access=Access.W, together=frozenset({"dry_flow"})
        ),
        SignalSpec(name="blend_flow", quantity=FLOW, access=Access.RW),
        SignalSpec(name="expected_humidity", quantity=HUMIDITY, access=Access.RP),
    )

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.supply: dict[str, float] = {}
        self.target = 50.0
        self.blend_flow = 1.0
        self.pump_writes: list[tuple[float, float]] = []

    def observe(self, reading: Reading) -> None:
        for role, signal in self.bound.items():
            if reading.signal is signal:
                self.supply[role] = reading.value

    def commit(self, time_ns: int) -> Mapping[Signal, WriteState]:
        pending = {signal.name: value for signal, value in self.pending.items()}
        self.target = pending.get("humidity", self.target)
        self.blend_flow = pending.get("blend_flow", self.blend_flow)
        self.pump_writes.append((self.blend_flow, self.target))
        return self.flush_pending()


class TestBinding:
    def test_a_static_tree_is_bound_on_construction(self):
        sensors = HumSensors("hum")
        assert sensors.root.address == "hum" and sensors.root.path == ""
        assert sensors.root.spec is None and sensors.root.device is sensors
        assert list(sensors.nodes) == ["chamber", "dry", "wet"]
        assert list(sensors.signals) == [
            "chamber.humidity",
            "chamber.temperature",
            "dry.humidity",
            "dry.temperature",
            "wet.humidity",
            "wet.temperature",
        ]
        dry = sensors.nodes["dry"]
        assert dry.address == "hum.dry" and dry.parent is sensors.root and dry.atomic is True
        assert sensors.signals["dry.humidity"].address == "hum.dry.humidity"
        assert sensors.signals["dry.humidity"].node is dry
        assert dry.signals["humidity"] is sensors.signals["dry.humidity"]
        assert sensors.root.children["dry"] is dry and sensors.root.signals == {}

    def test_a_dynamic_tree_is_bound_by_the_driver(self):
        furnace = SimFurnace("furnace", zones=3, power_w=(2500.0, 6000.0, 2000.0))
        assert list(furnace.signals) == [
            "zone1",
            "zone2",
            "zone3",
            "sample",
            "heater1",
            "heater2",
            "heater3",
        ]
        assert furnace.nodes == {}
        assert furnace.signals["heater2"].limits == (0.0, 6000.0)
        assert furnace.signals["zone1"].address == "furnace.zone1"
        assert furnace.root.signals["sample"] is furnace.signals["sample"]

    def test_views_by_access(self):
        furnace = SimFurnace("f", zones=2, power_w=(1.0, 1.0))
        assert list(furnace.publishing) == ["zone1", "zone2", "sample"]
        assert list(furnace.readable) == ["zone1", "zone2", "sample"]
        assert list(furnace.writable) == ["heater1", "heater2"]
        blender = Blender("b")
        assert list(blender.writable) == ["humidity", "dry_flow", "wet_flow", "blend_flow"]
        assert list(blender.readable) == ["blend_flow", "expected_humidity"]
        assert list(blender.publishing) == ["expected_humidity"]

    def test_a_namespace_two_deep(self):
        class Stage(HumSensors):
            TREE = (
                NodeSpec(
                    name="left",
                    children=(
                        NodeSpec(
                            name="dry",
                            children=(
                                SignalSpec(name="humidity", quantity=HUMIDITY, access=Access.RP),
                            ),
                        ),
                    ),
                ),
            )

        stage = Stage("stage")
        assert list(stage.nodes) == ["left", "left.dry"]
        assert stage.nodes["left.dry"].address == "stage.left.dry"
        assert stage.signals["left.dry.humidity"].address == "stage.left.dry.humidity"
        assert stage.signals["left.dry.humidity"].path == "left.dry.humidity"
        assert list(stage.root.walk()) == [stage.signals["left.dry.humidity"]]

    def test_duplicate_names_are_refused(self):
        class Twice(Device):
            TREE = (
                SignalSpec(name="x", quantity=TEMP, access=Access.R),
                NodeSpec(name="x", children=()),
            )

        with pytest.raises(ValueError, match="'d.x' is declared twice"):
            Twice("d")

    def test_a_bare_device_has_no_tree(self):
        bare = Device("bare")
        assert bare.TREE == () and bare.pending == {} and bare.bound == {}
        assert bare.poll_s is None and bare.label is None
        assert not hasattr(bare, "root")


class TestPollPeriod:
    def test_inherited_down_the_tree_with_overrides(self):
        sensors = HumSensors("hum")
        assert sensors.poll_s is None and sensors.signals["dry.humidity"].poll_s is None
        sensors.poll_s = 1.0
        assert sensors.root.poll_s == 1.0
        assert sensors.nodes["dry"].poll_s == 1.0
        assert sensors.signals["dry.humidity"].poll_s == 1.0
        sensors.nodes["dry"].override(poll_s=5.0)
        assert sensors.signals["dry.humidity"].poll_s == 5.0
        assert sensors.signals["dry.temperature"].poll_s == 5.0
        assert sensors.signals["wet.humidity"].poll_s == 1.0
        sensors.signals["dry.temperature"].override(poll_s=0.5)
        assert sensors.signals["dry.temperature"].poll_s == 0.5
        assert sensors.signals["dry.humidity"].poll_s == 5.0


class TestWriteSide:
    def test_apply_records_and_commit_writes_through(self):
        furnace = SimFurnace("f", zones=2, power_w=(2500.0, 6000.0))
        h1, h2 = furnace.signals["heater1"], furnace.signals["heater2"]
        furnace.apply(h1, 10, 1250.0)
        furnace.apply(h2, 10, 6000.0)
        assert furnace.pending == {h1: 1250.0, h2: 6000.0}
        assert furnace.written == {}
        states = furnace.commit(10)
        assert states == {
            h1: WriteState(value=1250.0, requested=None, at_limit=None, controller=None),
            h2: WriteState(value=6000.0, requested=None, at_limit="high", controller=None),
        }
        assert furnace.pending == {}
        assert furnace.written == states
        assert furnace.inputs == {"heater1": 0.5, "heater2": 1.0}

        furnace.apply(h1, 20, 0.0)
        assert furnace.commit(20) == {h1: WriteState(value=0.0, at_limit="low")}
        assert furnace.written[h1].at_limit == "low" and furnace.written[h2].value == 6000.0

    def test_the_base_commit_holds_the_value(self):
        class Holder(Device):
            TREE = (SignalSpec(name="setpoint", quantity=TEMP, access=Access.RW),)

        holder = Holder("h")
        setpoint = holder.signals["setpoint"]
        holder.apply(setpoint, 1, 50.0)
        assert holder.commit(1) == {setpoint: WriteState(value=50.0)}
        assert holder.written[setpoint].value == 50.0 and holder.pending == {}
        assert holder.commit(2) == {}

    def test_a_composite_device_sees_every_pending_value_at_once(self):
        blender = Blender("b")
        blender.bound = {"dry": HumSensors("hum").signals["dry.humidity"]}
        humidity, flow = blender.signals["humidity"], blender.signals["blend_flow"]
        blender.apply(humidity, 1, 47.0)
        blender.apply(flow, 1, 1.5)
        assert blender.pump_writes == []
        states = blender.commit(1)
        assert blender.pump_writes == [(1.5, 47.0)]
        assert states[humidity] == WriteState(value=47.0)
        assert states[flow] == WriteState(value=1.5)
        assert blender.pending == {}

    def test_observe_on_a_device_with_a_bound_input(self):
        blender = Blender("b")
        sensors = HumSensors("hum")
        blender.bound = {"dry": sensors.signals["dry.humidity"]}
        blender.observe(Reading(sensors.signals["dry.humidity"], 5, 3.0))
        assert blender.supply == {"dry": 3.0}
        assert blender.commit(5) == {}
        assert blender.pump_writes == [(1.0, 50.0)], "a new supply reading costs one pump write"

    def test_the_defaults_refuse_what_the_device_did_not_declare(self):
        bare = Device("bare")
        with pytest.raises(NotImplementedError, match="nothing to read"):
            next(bare.read(0))
        with pytest.raises(NotImplementedError, match="no bound input"):
            bare.observe(Reading(HumSensors("h").signals["dry.humidity"], 0, 0.0))

    def test_read_yields_samples_on_the_root(self):
        furnace = SimFurnace("f", zones=1, power_w=(1.0,))
        samples = list(furnace.read(7))
        assert len(samples) == 1 and samples[0].node is furnace.root
        assert samples[0].values == {"zone1": 20.0, "sample": 20.0}
        assert [r.signal.address for r in samples[0].readings()] == ["f.zone1", "f.sample"]


class FurnaceConfig(DriverConfig[SimFurnace]):
    zones: int = 3
    power_w: tuple[float, ...] = (2500.0, 6000.0, 2000.0)

    def build(self, name: str, label: str | None = None) -> SimFurnace:
        return SimFurnace(name, zones=self.zones, power_w=self.power_w, label=label)


class SensorsConfig(DriverConfig[HumSensors]):
    def build(self, name: str, label: str | None = None) -> HumSensors:
        return HumSensors(name, label=label)


@pytest.fixture
def furnace_tag(fresh) -> str:
    tag = fresh("sim_furnace")

    class Tagged(FurnaceConfig, tag=tag):
        pass

    return tag


@pytest.fixture
def sensors_tag(fresh) -> str:
    tag = fresh("sht4x_set")

    class Tagged(SensorsConfig, tag=tag):
        pass

    return tag


class TestDeviceEntry:
    def test_flat_and_layered_parse_to_the_same_thing(self):
        flat = DeviceEntry.model_validate({
            "driver": "sht4x",
            "label": "Wet supply",
            "poll_s": 5,
            "link": "i2c1",
            "address": 0x46,
        })
        layered = DeviceEntry.model_validate({
            "driver": "sht4x",
            "label": "Wet supply",
            "poll_s": 5,
            "config": {"link": "i2c1", "address": 0x46},
        })
        assert flat == layered
        assert flat.config == {"link": "i2c1", "address": 70}
        assert flat.driver == "sht4x" and flat.poll_s == 5.0 and flat.label == "Wet supply"
        assert flat.signals == {} and flat.bound == {}

    def test_config_plus_a_leftover_key_is_an_error(self):
        with pytest.raises(ValidationError, match="address beside `config`"):
            DeviceEntry.model_validate({
                "driver": "sht4x",
                "config": {"link": "i2c1"},
                "address": 0x46,
            })

    def test_envelope_keys_are_reserved_in_a_driver_config(self):
        with pytest.raises(TypeError, match="Clashing: signals is an envelope key"):

            class Clashing(DriverConfig[SimFurnace]):
                signals: dict[str, str] = {}

        with pytest.raises(TypeError, match="poll_s is an envelope key"):

            class Polling(DriverConfig[SimFurnace]):
                poll_s: float = 1.0

        assert "link" in DriverConfig.model_fields, "the driver's own keys are not reserved"

    def test_the_plan_s_layered_example_parses(self):
        entry = DeviceEntry.model_validate({
            "driver": "sht4x_set",
            "label": "Humidity sensors",
            "poll_s": 1,
            "config": {"link": "i2c1"},
            "signals": {
                "chamber": {
                    "config": {"address": 0x44},
                    "signals": {"humidity": {"warn": [20, 80]}},
                },
                "dry": {"poll_s": 5, "config": {"address": 0x45}},
                "wet": {"poll_s": 5, "config": {"address": 0x46}},
            },
        })
        chamber = entry.signals["chamber"]
        assert chamber.config == {"address": 0x44}  # type: ignore[union-attr]
        assert chamber.signals["humidity"].warn == (20.0, 80.0)  # type: ignore[union-attr]
        assert entry.signals["dry"].poll_s == 5.0

    def test_build_looks_the_driver_up_and_applies_the_envelope(self, furnace_tag):
        entry = DeviceEntry.model_validate({
            "driver": furnace_tag,
            "label": "Tube furnace",
            "poll_s": 1,
            "zones": 2,
            "power_w": [2500, 6000],
            "signals": {
                "zone1": {"label": "Zone 1 (entry)", "range": [0, 1200], "precision": 1},
                "sample": {"poll_s": 2, "warn": [0, 1100]},
                "heater2": {"limits": [0, 5000], "together": ["heater1"]},
            },
        })
        furnace = entry.build("furnace")
        assert isinstance(furnace, SimFurnace)
        assert furnace.name == "furnace" and furnace.label == "Tube furnace"
        assert furnace.zones == 2 and furnace.poll_s == 1.0
        zone1 = furnace.signals["zone1"]
        assert zone1.label == "Zone 1 (entry)" and zone1.spec.range == (0.0, 1200.0)
        assert zone1.spec.precision == 1 and zone1.poll_s == 1.0
        assert furnace.signals["sample"].poll_s == 2.0
        assert furnace.signals["sample"].spec.warn == (0.0, 1100.0)
        assert furnace.signals["heater2"].limits == (0.0, 5000.0)
        assert furnace.signals["heater2"].spec.together == frozenset({"heater1"})
        assert furnace.signals["heater1"].limits == (0.0, 2500.0), "untouched"
        assert furnace.signals["zone1"].access is Access.RP, "untouched"

    def test_namespace_overrides_recurse(self, sensors_tag):
        entry = DeviceEntry.model_validate({
            "driver": sensors_tag,
            "poll_s": 1,
            "signals": {
                "chamber": {"label": "Chamber", "signals": {"humidity": {"warn": [20, 80]}}},
                "dry": {"poll_s": 5, "signals": {"temperature": {"poll_s": 10}}},
                "wet": {"poll_s": 5},
            },
        })
        sensors = entry.build("hum")
        assert sensors.nodes["chamber"].label == "Chamber"
        assert sensors.signals["chamber.humidity"].spec.warn == (20.0, 80.0)
        assert sensors.signals["chamber.humidity"].poll_s == 1.0
        assert sensors.signals["dry.humidity"].poll_s == 5.0
        assert sensors.signals["dry.temperature"].poll_s == 10.0
        assert sensors.signals["wet.humidity"].poll_s == 5.0, "a bare poll_s on a namespace"

    def test_unknown_names_error_with_the_address(self, furnace_tag, sensors_tag):
        entry = DeviceEntry.model_validate({
            "driver": furnace_tag,
            "signals": {"zone9": {"label": "x"}},
        })
        with pytest.raises(ValueError, match="'furnace.zone9' is not a signal or namespace"):
            entry.build("furnace")
        entry = DeviceEntry.model_validate({
            "driver": sensors_tag,
            "signals": {"dry": {"signals": {"pressure": {"precision": 1}}}},
        })
        with pytest.raises(ValueError, match="'hum.dry.pressure' is not a signal or namespace"):
            entry.build("hum")

    def test_a_namespace_and_a_signal_are_told_apart(self, sensors_tag):
        entry = DeviceEntry.model_validate({
            "driver": sensors_tag,
            "signals": {"dry": {"range": [0, 100]}},
        })
        with pytest.raises(ValueError, match="'hum.dry' is a namespace: range is a signal's"):
            entry.build("hum")
        entry = DeviceEntry.model_validate({
            "driver": sensors_tag,
            "signals": {"dry": {"signals": {"humidity": {"signals": {}}}}},
        })
        with pytest.raises(ValueError, match="'hum.dry.humidity' is a signal, not a namespace"):
            entry.build("hum")

    def test_access_can_be_removed_but_not_added(self, furnace_tag):
        entry = DeviceEntry.model_validate({
            "driver": furnace_tag,
            "signals": {"sample": {"publishing": False}, "zone2": {"access": "r"}},
        })
        furnace = entry.build("furnace")
        assert furnace.signals["sample"].access is Access.R
        assert furnace.signals["sample"].spec.access is Access.RP, "the driver's stays"
        assert furnace.signals["zone2"].access is Access.R
        assert "zone2" not in furnace.publishing and "zone2" in furnace.readable

        entry = DeviceEntry.model_validate({
            "driver": furnace_tag,
            "signals": {"zone1": {"access": "rpw"}},
        })
        with pytest.raises(ValueError, match="'furnace.zone1' cannot add access w"):
            entry.build("furnace")

        entry = DeviceEntry.model_validate({
            "driver": furnace_tag,
            "signals": {"zone1": {"readable": False}},
        })
        with pytest.raises(
            ValueError, match="'furnace.zone1': readable: false leaves it publishing"
        ):
            entry.build("furnace")

        with pytest.raises(ValidationError, match="only `false` is allowed"):
            DeviceEntry.model_validate({
                "driver": furnace_tag,
                "signals": {"heater1": {"publishing": True}},
            })
        with pytest.raises(ValidationError, match="P without R"):
            DeviceEntry.model_validate({
                "driver": furnace_tag,
                "signals": {"zone1": {"access": "pw"}},
            })

    def test_build_refuses_an_unknown_or_legacy_driver(self, fresh):
        with pytest.raises(ValueError, match="driver 'no_such' is not registered"):
            DeviceEntry(driver="no_such").build("x")

        from flyball.core.device import DeviceConfig

        tag = fresh("legacy")

        class Legacy(DeviceConfig[SimFurnace], tag=tag):
            pass

        assert Config.registry[tag] is Legacy
        with pytest.raises(ValueError, match=f"driver '{tag}' is a Legacy, not a device driver"):
            DeviceEntry(driver=tag).build("x")

    def test_the_driver_config_is_validated(self, furnace_tag):
        entry = DeviceEntry.model_validate({"driver": furnace_tag, "zones": "three"})
        with pytest.raises(ValidationError, match="zones"):
            entry.build("furnace")
