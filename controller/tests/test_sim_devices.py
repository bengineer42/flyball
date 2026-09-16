"""The simulated drivers: `sim_daq` reads a plant's outputs, `sim_drive` sets its inputs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from flyball.control.feedforward import Affine
from flyball.control.laws import PI, P
from flyball.core.device import DeviceEntry
from flyball.core.errors import ConflictError, HardwareError, NotFoundError
from flyball.core.signal import Access, Sample, Signal
from flyball.runtime.config import RigConfig
from flyball.runtime.rig import Rig
from flyball.sim import (
    DaqPort,
    DrivePort,
    Furnace,
    FurnaceConfig,
    Noisy,
    PlantConfig,
    SimDaqConfig,
    SimDriveConfig,
    SteppedClock,
)
from flyball.sim.devices import Drive
from flyball.sim.furnace import MultiPlant


class Chamber:
    """A tiny `MultiPlant`: two supplies blended by one input, read on three named ports."""

    def __init__(self) -> None:
        self.inputs = {"wet_fraction": 0.0}
        self.output_names = ("chamber", "dry", "wet")
        self.humidity = 10.0
        self.advanced: list[int] = []

    def output(self, port: str) -> float:
        return {"chamber": self.humidity, "dry": 10.0, "wet": 90.0}[port]

    def advance(self, time_ns: int) -> None:
        if time_ns not in self.advanced:
            self.advanced.append(time_ns)
            self.humidity = 10.0 + 80.0 * self.inputs["wet_fraction"]

    def feedforward(self, port: str, demand: float) -> float:
        return (demand - 10.0) / 80.0

    def inverse_feedforward(self, port: str, drive: float) -> float:
        return 10.0 + 80.0 * drive


def _built(config, name: str, plant):
    """A device from `config`, its link replaced by the built plant as the rig file's path does."""
    return config.model_copy(update={"link": plant}).build(name)


@pytest.fixture
def tube() -> Furnace:
    return FurnaceConfig(zones=3, power_w=[2500, 6000, 2000], sensor_lag_s=0, noise=0).build()


class TestSimDaq:
    def test_reads_a_furnace_s_ports_as_rp_temperature_signals(self, tube):
        daq = _built(
            SimDaqConfig(link="tube", ports={"z1": "zone1", "sample": "sample"}), "f", tube
        )
        assert [str(s) for s in daq.signals] == ["z1", "sample"]
        z1 = daq.signals["z1"]
        assert z1.address == "f.z1" and z1.access == Access.RP and z1.unit.symbol == "°C"
        assert z1.quantity.name == "temperature" and z1.spec.range is None
        (sample,) = daq.read(0)
        assert sample.node is daq.root and sample.by_name() == {"z1": 20.0, "sample": 20.0}
        assert daq.state.outputs == {"z1": 20.0, "sample": 20.0} and daq.state.broken == ()
        assert daq.config.ports == {"z1": "zone1", "sample": "sample"} and daq.config.link == ""

    def test_advances_the_plant_by_the_time_since_the_last_read(self, tube):
        daq = _built(SimDaqConfig(link="tube", ports={"z1": "zone1"}), "f", tube)
        tube.inputs["heater1"] = 1.0
        (first,) = daq.read(0)
        (later,) = daq.read(600_000_000_000)
        assert first.values[daq.signals["z1"]] == 20.0
        assert later.values[daq.signals["z1"]] > 100
        assert list(daq.read(600_000_000_000)) and tube.temperature[0] == pytest.approx(
            later.values[daq.signals["z1"]]
        ), "the same instant again steps nothing"

    def test_a_bare_plant_needs_its_quantity_spelled_out(self):
        plant = PlantConfig(model="lag", initial=5.0)
        with pytest.raises(ValueError, match="say `\\{port, quantity, unit\\}`"):
            SimDaqConfig(link=plant, ports={"t": "output"}).build("tc")
        with pytest.raises(ValueError, match="both `quantity` and `unit`"):
            SimDaqConfig(link=plant, ports={"t": DaqPort(port="output", unit="°C")}).build("tc")
        with pytest.raises(ValueError, match="no output port 'zone1'"):
            SimDaqConfig(link=plant, ports={"t": DaqPort(port="zone1")}).build("tc")
        daq = SimDaqConfig(
            link=plant,
            ports={
                "t": {"port": "output", "quantity": "temperature", "unit": "°C", "range": [0, 9]}
            },
        ).build("tc")
        assert isinstance(daq.plant, Noisy)
        t = daq.signals["t"]
        assert t.unit.symbol == "°C" and t.spec.range == (0.0, 9.0) and t.access == Access.RP
        (sample,) = daq.read(0)
        assert sample.values[t] == 5.0
        with pytest.raises(ValueError, match="at least one port"):
            SimDaqConfig(link=plant, ports={}).build("tc")

    def test_a_furnace_port_may_still_say_what_it_is(self, tube):
        daq = _built(
            SimDaqConfig(
                link="tube", ports={"k": DaqPort(port="zone1", quantity="temperature", unit="K")}
            ),
            "f",
            tube,
        )
        assert daq.signals["k"].unit.symbol == "K"

    def test_the_poll_reads_each_signal_on_its_own_period(self, tube):
        daq = _built(
            SimDaqConfig(link="tube", ports={"z1": "zone1", "sample": "sample"}), "f", tube
        )
        daq.poll_s = 1.0
        daq.signals["sample"].override(poll_s=2.0)
        second = 1_000_000_000
        assert list(daq.read(0))[0].by_name() == {"z1": 20.0, "sample": 20.0}
        (sample,) = daq.read(second)
        assert sample.by_name() == {"z1": 20.0}, "the sample thermocouple is not due yet"
        (sample,) = daq.read(2 * second)
        assert set(sample.by_name()) == {"z1", "sample"}
        (sample,) = daq.read(int(2.95 * second))
        assert set(sample.by_name()) == {"z1"}, "a little early still counts, a lot does not"
        # A node asked for by name is read whole: a fresh read wants a value now.
        (sample,) = daq.read(3 * second, daq.root)
        assert set(sample.by_name()) == {"z1", "sample"}
        daq.poll_s = None
        assert list(daq.read(3 * second)) and list(daq.read(3 * second)), (
            "with no period at all every poll reads everything"
        )

    def test_fail_and_restore_one_signal(self, tube):
        daq = _built(SimDaqConfig(link="tube", ports={"z1": "zone1", "z2": "zone2"}), "f", tube)
        list(daq.read(5_000_000_000))
        state = daq.fail("z2")
        assert state.broken == ("z2",)
        (condition,) = state.conditions
        assert condition.kind == "broken" and condition.since_ns == 5_000_000_000
        assert "z2" in condition.message
        with pytest.raises(HardwareError, match="f.z2: thermocouple open circuit"):
            list(daq.read(6_000_000_000))
        assert daq.restore("z2").broken == () and daq.state.conditions == ()
        assert list(daq.read(6_000_000_000))
        with pytest.raises(NotFoundError, match="no signal 'z9'"):
            daq.fail("z9")
        assert {tag: spec.simulation for tag, spec in daq.commands.items()} == {
            "fail": True,
            "restore": True,
        }


class TestNamespaces:
    """A dotted port path is a namespace, so a sim overlay can mirror a real device's addresses."""

    def test_dotted_ports_make_atomic_namespaces_and_per_namespace_samples(self, tube):
        rig = Rig()
        rig.clock = SteppedClock(0)
        daq = _built(
            SimDaqConfig(
                link="tube",
                ports={"entry.zone": "zone1", "entry.sample": "sample", "exit.zone": "zone3"},
            ),
            "dev",
            tube,
        )
        rig.add_device(daq)
        assert list(daq.signals) == ["entry.zone", "entry.sample", "exit.zone"]
        assert list(daq.nodes) == ["entry", "exit"] and daq.nodes["entry"].atomic
        humidity = rig.resolve("dev.entry.zone")
        assert humidity is daq.signals["entry.zone"] and humidity.address == "dev.entry.zone"
        assert humidity.name == "zone" and humidity.unit.symbol == "°C"
        assert daq.ports == {"entry.zone": "zone1", "entry.sample": "sample", "exit.zone": "zone3"}
        samples = list(daq.read(1_000_000_000))
        assert [s.node.address for s in samples] == ["dev.entry", "dev.exit"]
        assert samples[0].by_name() == {"zone": 20.0, "sample": 20.0}
        assert samples[1].by_name() == {"zone": 20.0} and samples[1].time_ns == samples[0].time_ns
        sample = rig.read(daq.nodes["exit"], fresh=True)
        assert isinstance(sample, Sample) and sample.node is daq.nodes["exit"]
        assert sample.by_name() == {"zone": 20.0}
        assert rig.read(daq.nodes["entry"], fresh=True).by_name(daq.root) == {  # type: ignore[union-attr]
            "entry.zone": 20.0,
            "entry.sample": 20.0,
        }
        assert daq.state.outputs == {"entry.zone": 20.0, "entry.sample": 20.0, "exit.zone": 20.0}
        daq.fail("entry.sample")
        assert daq.state.broken == ("entry.sample",)
        with pytest.raises(HardwareError, match="dev.entry.sample"):
            list(daq.read(2_000_000_000, daq.nodes["entry"]))
        assert [s.node.address for s in daq.read(2_000_000_000, daq.nodes["exit"])] == ["dev.exit"]

    def test_flat_and_dotted_ports_may_mix(self, tube):
        daq = _built(
            SimDaqConfig(link="tube", ports={"sample": "sample", "z.one": "zone1"}), "d", tube
        )
        (root, z) = daq.read(0)
        assert root.node is daq.root and root.by_name() == {"sample": 20.0}
        assert z.node is daq.nodes["z"] and z.by_name() == {"one": 20.0}

    def test_a_dotted_drive_mirrors_a_real_device_s_write_addresses(self, tube):
        rig = Rig()
        rig.clock = SteppedClock(0)
        drive = _built(
            SimDriveConfig(link="tube", ports={"bank.h1": "heater1", "bank.h2": "heater2"}),
            "heaters",
            tube,
        )
        rig.add_device(drive)
        h1 = rig.resolve("heaters.bank.h1")
        assert isinstance(h1, Signal) and h1.limits == (0.0, 2500.0)
        states = rig.demand(drive.nodes["bank"], {"h1": 1250.0, "h2": 300.0})
        assert states[h1].value == 1250.0 and tube.inputs["heater1"] == 0.5
        assert tube.inputs["heater2"] == 0.05 and drive.state.inputs == {
            "bank.h1": 0.5,
            "bank.h2": 0.05,
        }
        assert drive.disturb("bank.h2", 600.0).inputs["bank.h2"] == pytest.approx(0.15), "W"

    def test_a_path_cannot_be_both_a_namespace_and_a_signal(self, tube):
        with pytest.raises(ValueError, match="d.a: both a namespace and a signal"):
            _built(SimDaqConfig(link="tube", ports={"a": "zone1", "a.b": "zone2"}), "d", tube)
        with pytest.raises(ValueError, match="d.a: both a namespace and a signal"):
            _built(SimDaqConfig(link="tube", ports={"a.b": "zone2", "a": "zone1"}), "d", tube)
        with pytest.raises(ValueError, match="'a..b' is not a signal path"):
            _built(SimDriveConfig(link="tube", ports={"a..b": "heater1"}), "d", tube)

    def test_the_rig_file_overrides_reach_a_namespaced_signal(self):
        config = RigConfig.model_validate({
            "links": {"plant": {"tag": "sim_furnace", "zones": 2}},
            "devices": {
                "hum_sensors": {
                    "driver": "sim_daq",
                    "poll_s": 1,
                    "config": {"link": "plant", "ports": {"dry.t": "zone1", "wet.t": "zone2"}},
                    "signals": {
                        "dry": {"signals": {"t": {"warn": [0, 100]}}},
                        "wet": {"poll_s": 5},
                    },
                },
            },
        })
        rig = config.build(start=False)
        assert rig.resolve("hum_sensors.dry.t").spec.warn == (0.0, 100.0)
        assert rig.resolve("hum_sensors.wet.t").poll_s == 5


class TestAnyMultiPlant:
    """Any `MultiPlant` with named ports will do, not only the furnace: a sim overlay's plant."""

    def test_a_daq_reads_named_ports_with_the_quantity_spelled_out(self):
        chamber = Chamber()
        assert isinstance(chamber, MultiPlant)
        with pytest.raises(ValueError, match="'chamber' has no quantity of its own"):
            _built(SimDaqConfig(link="c", ports={"chamber.humidity": "chamber"}), "hum", chamber)
        with pytest.raises(
            ValueError, match="no output port 'zone1'; there are \\('chamber', 'dry', 'wet'\\)"
        ):
            _built(SimDaqConfig(link="c", ports={"x": "zone1"}), "hum", chamber)
        rh = {"quantity": "humidity", "unit": "%"}
        daq = _built(
            SimDaqConfig(
                link="c",
                ports={
                    "chamber.humidity": {"port": "chamber", **rh},
                    "dry.humidity": {"port": "dry", **rh},
                    "wet.humidity": {"port": "wet", **rh},
                },
            ),
            "hum_sensors",
            chamber,
        )
        assert [s.address for s in daq.root.walk()] == [
            "hum_sensors.chamber.humidity",
            "hum_sensors.dry.humidity",
            "hum_sensors.wet.humidity",
        ]
        samples = list(daq.read(1_000_000_000))
        assert [(s.node.address, s.by_name()) for s in samples] == [
            ("hum_sensors.chamber", {"humidity": 10.0}),
            ("hum_sensors.dry", {"humidity": 10.0}),
            ("hum_sensors.wet", {"humidity": 90.0}),
        ]
        assert chamber.advanced == [1_000_000_000], "advanced once for the three samples"

    def test_a_drive_on_named_inputs_is_a_fraction_of_full(self):
        chamber = Chamber()
        with pytest.raises(
            ValueError, match="no input port 'input'; there are \\('wet_fraction',\\)"
        ):
            _built(SimDriveConfig(link="c", ports={"drive": "input"}), "blender", chamber)
        drive = _built(
            SimDriveConfig(link="c", ports={"wet_fraction": "wet_fraction"}), "blender", chamber
        )
        signal = drive.signals["wet_fraction"]
        assert signal.unit is Drive and signal.limits == (0.0, 1.0)
        drive.apply(signal, 0, 0.5)
        drive.commit(0)
        assert chamber.inputs == {"wet_fraction": 0.5} and drive.state.inputs == {
            "wet_fraction": 0.5
        }
        assert drive.disturb("wet_fraction", 0.25).inputs == {"wet_fraction": 0.75}

    def test_a_long_form_drive_port_is_set_in_its_own_unit_and_mapped_onto_the_drive(self):
        chamber = Chamber()
        rig = Rig()
        rig.clock = SteppedClock(0)
        drive = _built(
            SimDriveConfig(
                link="c",
                ports={
                    "humidity": {
                        "port": "wet_fraction",
                        "quantity": "humidity",
                        "unit": "%",
                        "limits": [0, 100],
                    }
                },
            ),
            "blender",
            chamber,
        )
        rig.add_device(drive)
        humidity = rig.resolve("blender.humidity")
        assert isinstance(humidity, Signal) and str(humidity.access) == "w"
        assert humidity.unit.symbol == "%" and humidity.limits == (0.0, 100.0)
        assert humidity.quantity.name == "humidity"
        (state,) = rig.demand(drive.root, {"humidity": 25.0}).values()
        assert state.value == 25.0 and chamber.inputs == {"wet_fraction": 0.25}
        assert drive.state.inputs == {"humidity": 0.25}
        assert drive.disturb("humidity", 10.0).inputs == {"humidity": pytest.approx(0.35)}
        (state,) = rig.demand(drive.root, {"humidity": 150.0}).values()
        assert state.at_limit == "high" and chamber.inputs["wet_fraction"] == 1.0
        assert drive.ports == {"humidity": "wet_fraction"}
        assert drive.config.ports["humidity"] == DrivePort(
            port="wet_fraction", quantity="humidity", unit="%", limits=(0.0, 100.0)
        )
        with pytest.raises(ValueError, match="rising span"):
            DrivePort(port="wet_fraction", quantity="humidity", unit="%", limits=(1, 0))

    def test_a_furnace_heater_s_disturb_is_in_watts(self, tube):
        drive = _built(SimDriveConfig(link="tube", ports={"h1": "heater1"}), "heaters", tube)
        assert drive.disturb("h1", 250.0).inputs == {"h1": pytest.approx(0.1)}, "of 2500 W"

    def test_a_controller_closes_the_loop_through_a_shared_multi_plant(self):
        rig = Rig()
        clock = rig.clock = SteppedClock(0)
        chamber = Chamber()
        daq = _built(
            SimDaqConfig(
                link="c",
                ports={
                    "chamber.humidity": {"port": "chamber", "quantity": "humidity", "unit": "%"}
                },
            ),
            "hum_sensors",
            chamber,
        )
        daq.poll_s = 1.0
        drive = _built(
            SimDriveConfig(link="c", ports={"wet_fraction": "wet_fraction"}), "blender", chamber
        )
        for device in (daq, drive):
            rig.add_device(device)
            rig.start_polling(device)
        controller = rig.attach_controller(
            rig.resolve("blender.wet_fraction"),  # type: ignore[arg-type]
            rig.resolve("hum_sensors.chamber.humidity"),  # type: ignore[arg-type]
            law=P(kp=0.001),
            feedforward=Affine(gain=0.0125, bias=-0.125),  # the chamber's static inverse
        )
        controller.regulate(50.0)
        clock.advance(10)
        assert rig.latest[daq.signals["chamber.humidity"]].value == pytest.approx(50.0, abs=1.0)
        assert chamber.inputs["wet_fraction"] == pytest.approx(0.5, abs=0.02)


class TestSimDrive:
    def test_furnace_heaters_are_w_signals_in_watts_with_the_zone_s_power_as_limits(self, tube):
        drive = _built(
            SimDriveConfig(link="tube", ports={"h1": "heater1", "h2": "heater2"}), "heaters", tube
        )
        h1, h2 = drive.signals["h1"], drive.signals["h2"]
        assert h1.access == Access.W and h1.unit.symbol == "W" and h1.quantity.name == "power"
        assert h1.limits == (0.0, 2500.0) and h2.limits == (0.0, 6000.0)
        drive.apply(h1, 0, 1250.0)
        drive.apply(h2, 0, 6000.0)
        states = drive.commit(0)
        assert tube.inputs == {"heater1": 0.5, "heater2": 1.0, "heater3": 0.0}
        assert states[h1].value == 1250.0 and states[h1].at_limit is None
        assert states[h2].at_limit == "high" and drive.written[h2] is states[h2]
        assert drive.state.inputs == {"h1": 0.5, "h2": 1.0}
        with pytest.raises(ValueError, match="no input port 'zone1'"):
            _built(SimDriveConfig(link="tube", ports={"x": "zone1"}), "d", tube)

    def test_a_bare_plant_s_drive_is_a_fraction_of_full(self):
        plant = PlantConfig(model="lag", gain=10.0)
        drive = SimDriveConfig(link=plant, ports={"drive": "input"}).build("heater")
        signal = drive.signals["drive"]
        assert signal.unit is Drive and signal.limits == (0.0, 1.0) and signal.access == Access.W
        drive.apply(signal, 0, 0.25)
        drive.commit(0)
        assert drive.plant.input == 0.25
        assert drive.disturb("drive", -0.05).inputs == {"drive": 0.2}
        with pytest.raises(NotFoundError, match="no signal 'x'"):
            drive.disturb("x", 1.0)
        with pytest.raises(ValueError, match="no input port 'heater1'"):
            SimDriveConfig(link=plant, ports={"drive": "heater1"}).build("heater")

    def test_the_rig_clamps_a_demand_to_the_limits_before_the_drive_sees_it(self, tube):
        rig = Rig()
        rig.clock = SteppedClock(0)
        drive = _built(SimDriveConfig(link="tube", ports={"h3": "heater3"}), "heaters", tube)
        rig.add_device(drive)
        (state,) = rig.demand(drive.root, {"h3": 5000.0}).values()
        assert state.value == 2000.0 and state.requested == 5000.0 and state.at_limit == "high"
        assert tube.inputs["heater3"] == 1.0
        (state,) = rig.demand(drive.root, {"h3": -1.0}).values()
        assert state.value == 0.0 and state.at_limit == "low" and tube.inputs["heater3"] == 0.0


class TestSharedPlant:
    def test_two_devices_on_one_furnace_interact_through_it(self, tube):
        """The heaters drive the plant the daq reads, and the zones conduct heat to each other."""
        rig = Rig()
        clock = rig.clock = SteppedClock(0)
        daq = _built(
            SimDaqConfig(link="tube", ports={"zone1": "zone1", "zone2": "zone2", "zone3": "zone3"}),
            "furnace",
            tube,
        )
        daq.poll_s = 1.0
        drive = _built(
            SimDriveConfig(link="tube", ports={"heater1": "heater1", "heater3": "heater3"}),
            "heaters",
            tube,
        )
        for device in (daq, drive):
            rig.add_device(device)
            rig.start_polling(device)
        assert clock.scheduled == 1, "only the daq publishes, so only it is polled"
        rig.demand(drive.root, {"heater1": 2500.0})
        clock.advance(600)
        zones = {
            n: rig.latest[rig.resolve(f"furnace.{n}")].value for n in ("zone1", "zone2", "zone3")
        }
        assert zones["zone1"] > 100 and zones["zone1"] > zones["zone2"] > zones["zone3"] > 20
        assert len(rig.recent_readings(rig.resolve("furnace.zone2"))) == 60

    def test_a_controller_closes_the_loop_through_the_plant(self, tube):
        rig = Rig()
        clock = rig.clock = SteppedClock(0)
        daq = _built(SimDaqConfig(link="tube", ports={"zone2": "zone2"}), "furnace", tube)
        daq.poll_s = 1.0
        drive = _built(SimDriveConfig(link="tube", ports={"heater2": "heater2"}), "heaters", tube)
        for device in (daq, drive):
            rig.add_device(device)
            rig.start_polling(device)
        controller = rig.attach_controller(
            drive.signals["heater2"],
            daq.signals["zone2"],
            law=PI(kp=100, ki=0.15, tt=30),
        )
        assert controller.name == "heaters.heater2"
        controller.regulate(300.0)
        clock.advance(1800)
        assert rig.latest[daq.signals["zone2"]].value == pytest.approx(300, abs=20)
        assert 0.0 < tube.inputs["heater2"] < 1.0
        with pytest.raises(ConflictError, match="driven by controller 'heaters.heater2'"):
            rig.demand(drive.root, {"heater2": 0.0})


class TestRigFile:
    def test_the_plan_s_sim_overlay_shape_builds(self):
        config = RigConfig.model_validate({
            "clock": {"speed": 60},
            "links": {"plant": {"tag": "sim_furnace", "zones": 2, "power_w": [2500, 6000]}},
            "devices": {
                "furnace": {
                    "driver": "sim_daq",
                    "poll_s": 1,
                    "config": {"link": "plant", "ports": {"zone1": "zone1", "zone2": "zone2"}},
                    "signals": {"zone1": {"range": [0, 1200], "warn": [0, 1100]}},
                },
                "heaters": {
                    "driver": "sim_drive",
                    "link": "plant",
                    "ports": {"heater1": "heater1", "heater2": "heater2"},
                    "signals": {"heater2": {"limits": [0, 3000]}},
                },
            },
            "controllers": {"heaters.heater1": {"signal": "furnace.zone1"}},
        })
        assert config.simulated
        rig = config.build(start=False)
        assert rig.resolve("furnace.zone1").spec.warn == (0.0, 1100.0)
        assert rig.resolve("heaters.heater2").limits == (0.0, 3000.0), "the file narrowed it"
        assert rig.resolve("heaters.heater1").limits == (0.0, 2500.0)
        assert rig.devices["furnace"].plant is rig.devices["heaters"].plant is rig.links["plant"]
        assert list(rig.controllers) == ["heaters.heater1"]

    def test_a_plant_s_model_field_is_model_not_kind(self):
        with pytest.raises(ValidationError, match="kind"):
            PlantConfig.model_validate({"kind": "lag"})
        assert PlantConfig.model_validate({"model": "fopdt", "dead_s": 2}).model == "fopdt"

    def test_an_undeclared_link_or_port_is_named(self):
        entry = DeviceEntry.model_validate({
            "driver": "sim_daq",
            "link": "ghost",
            "ports": {"a": "b"},
        })
        with pytest.raises(NotFoundError, match="link 'ghost' is not declared"):
            entry.build("d", {})
        entry = DeviceEntry.model_validate({"driver": "sim_daq", "link": "p", "ports": {"a": "b"}})
        with pytest.raises(ValueError, match="no output port 'b'"):
            entry.build("d", {"p": FurnaceConfig().build()})
