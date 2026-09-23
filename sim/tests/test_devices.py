"""The simulated drivers: `sim_daq` reads a plant's outputs, `sim_drive` sets its inputs.

Furnace-specific coverage (a real `MultiPlant` whose ports know their own
quantity) lives in `examples/furnace/tests/test_devices.py` instead --
`flyball-sim` has no furnace of its own, only the generic protocol.
"""

from __future__ import annotations

import math

import pytest
from flyball.control.feedforward import Affine
from flyball.control.laws import PI, P
from flyball.foundation.device import Access, Signal
from flyball.foundation.errors import NotFoundError
from flyball.rig import Rig
from pydantic import ValidationError

from flyball_sim import (
    DaqPort,
    DrivePort,
    Lag,
    Noisy,
    PlantConfig,
    SimDaqConfig,
    SimDriveConfig,
    SteppedClock,
)
from flyball_sim.devices import Drive


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


class TestAnyMultiPlant:
    """Any `MultiPlant` with named ports will do, not only a furnace: a sim overlay's plant."""

    def test_a_port_may_carry_its_label_and_tags(self):
        chamber = Chamber()
        rh = {"quantity": "humidity", "unit": "%"}
        daq = _built(
            SimDaqConfig(
                link="c",
                ports={
                    "dry.humidity": {
                        "port": "dry",
                        "label": "Dry line humidity",
                        "tags": {"line": "dry"},
                        **rh,
                    }
                },
            ),
            "hum",
            chamber,
        )
        signal = daq.signals["dry.humidity"]
        assert signal.label == "Dry line humidity" and signal.tags == {"line": "dry"}

    def test_a_daq_reads_named_ports_with_the_quantity_spelled_out(self):
        chamber = Chamber()
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
            "hum_sensors.conditions",
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
        assert chamber.inputs == {"wet_fraction": 0.5} and drive.inputs == {"wet_fraction": 0.5}
        assert drive.disturb("wet_fraction", 0.25) == {"wet_fraction": 0.75}

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
        assert isinstance(humidity, Signal) and str(humidity.access) == "rpw"
        assert humidity.unit.symbol == "%" and humidity.limits == (0.0, 100.0)
        assert humidity.quantity.name == "humidity"
        (state,) = rig.write(drive.root, {"humidity": 25.0}).values()
        assert state.value == 25.0 and chamber.inputs == {"wet_fraction": 0.25}
        assert drive.inputs == {"humidity": 0.25}
        assert drive.disturb("humidity", 10.0) == {"humidity": pytest.approx(0.35)}
        (state,) = rig.write(drive.root, {"humidity": 150.0}).values()
        # the disturb kick (fraction 0.1) survives this commit, on top of the clamped demand
        assert state.at_limit == "high"
        assert chamber.inputs["wet_fraction"] == pytest.approx(1.1)
        assert drive.ports == {"humidity": "wet_fraction"}
        assert drive.config.ports["humidity"] == DrivePort(
            port="wet_fraction", quantity="humidity", unit="%", limits=(0.0, 100.0)
        )
        with pytest.raises(ValueError, match="rising span"):
            DrivePort(port="wet_fraction", quantity="humidity", unit="%", limits=(1, 0))

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
    def test_a_bare_plant_s_drive_is_a_fraction_of_full(self):
        plant = PlantConfig(model="lag", gain=10.0)
        drive = SimDriveConfig(link=plant, ports={"drive": "input"}).build("heater")
        signal = drive.signals["drive"]
        assert signal.unit is Drive and signal.limits == (0.0, 1.0) and signal.access == Access.RPW
        drive.apply(signal, 0, 0.25)
        drive.commit(0)
        assert drive.plant.input == 0.25
        assert drive.disturb("drive", -0.05) == {"drive": 0.2}
        with pytest.raises(NotFoundError, match="no signal 'x'"):
            drive.disturb("x", 1.0)
        with pytest.raises(ValueError, match="no input port 'heater1'"):
            SimDriveConfig(link=plant, ports={"drive": "heater1"}).build("heater")


class TestSmartDrive:
    """A `demand: output` port takes a demand in the plant's output unit; `commit` inverts it."""

    def test_declared_in_the_output_unit_with_limits_from_the_plant_s_static_range(self):
        plant = PlantConfig(model="lag", tau_s=10, gain=80, ambient=20)
        drive = SimDriveConfig(
            link=plant,
            ports={
                "t": {"port": "input", "demand": "output", "quantity": "temperature", "unit": "°C"}
            },
        ).build("heater")
        signal = drive.signals["t"]
        assert signal.access == Access.RPW and signal.unit.symbol == "°C"
        assert signal.quantity.name == "temperature" and signal.limits == (20.0, 100.0)

    def test_a_negative_gain_still_sorts_ascending(self):
        plant = PlantConfig(model="lag", tau_s=10, gain=-30, ambient=22)
        drive = SimDriveConfig(
            link=plant,
            ports={
                "t": {"port": "input", "demand": "output", "quantity": "temperature", "unit": "°C"}
            },
        ).build("compressor")
        assert drive.signals["t"].limits == (-8.0, 22.0)

    def test_commit_inverts_the_plant_and_clamps_the_result_to_a_drive(self):
        plant = PlantConfig(model="lag", tau_s=10, gain=80, ambient=20)
        drive = SimDriveConfig(
            link=plant,
            ports={
                "t": {"port": "input", "demand": "output", "quantity": "temperature", "unit": "°C"}
            },
        ).build("heater")
        signal = drive.signals["t"]
        drive.apply(signal, 0, 60.0)
        drive.commit(0)
        assert drive.plant.input == pytest.approx(0.5), "(60 - 20) / 80"
        drive.apply(signal, 0, 900.0)  # well past the declared limits
        drive.commit(0)
        assert drive.plant.input == 1.0, "clamped, not > 1"
        drive.apply(signal, 0, -900.0)
        drive.commit(0)
        assert drive.plant.input == 0.0, "clamped, not < 0"

    def test_disturb_is_still_in_the_signal_s_unit(self):
        plant = PlantConfig(model="lag", tau_s=10, gain=80, ambient=20)
        drive = SimDriveConfig(
            link=plant,
            ports={
                "t": {"port": "input", "demand": "output", "quantity": "temperature", "unit": "°C"}
            },
        ).build("heater")
        drive.apply(drive.signals["t"], 0, 60.0)
        drive.commit(0)
        assert drive.disturb("t", 8.0) == {"t": pytest.approx(0.6)}, "8 / 80 more drive"

    def test_a_bare_plant_needs_its_quantity_spelled_out(self):
        plant = PlantConfig(model="lag", gain=80)
        with pytest.raises(ValueError, match="the plant has no quantity of its own"):
            SimDriveConfig(link=plant, ports={"t": {"port": "input", "demand": "output"}}).build(
                "heater"
            )
        with pytest.raises(ValueError, match="both `quantity` and `unit`"):
            SimDriveConfig(
                link=plant,
                ports={"t": {"port": "input", "demand": "output", "unit": "°C"}},
            ).build("heater")

    def test_without_a_static_inverse_limits_must_be_given(self):
        plant = PlantConfig(model="integrator", gain=2.0, leak=0.05)
        with pytest.raises(ValueError, match="the plant has no static range for 'input'; give"):
            SimDriveConfig(
                link=plant,
                ports={
                    "t": {"port": "input", "demand": "output", "quantity": "volume", "unit": "L"}
                },
            ).build("valve")
        drive = SimDriveConfig(
            link=plant,
            ports={
                "t": {
                    "port": "input",
                    "demand": "output",
                    "quantity": "volume",
                    "unit": "L",
                    "limits": [0, 40],
                }
            },
        ).build("valve")
        assert drive.signals["t"].limits == (0.0, 40.0)

    def test_the_input_form_still_needs_quantity_unit_and_limits_together(self):
        plant = PlantConfig(model="lag", gain=80)
        with pytest.raises(ValueError, match="say `quantity`, `unit` and `limits`"):
            SimDriveConfig(
                link=plant, ports={"t": {"port": "input", "quantity": "temperature"}}
            ).build("heater")

    def test_a_controller_with_the_setpoint_feedforward_settles_on_a_lag(self):
        rig = Rig()
        clock = rig.clock = SteppedClock(0)
        plant = PlantConfig(model="lag", tau_s=10, gain=80, ambient=20, noise=0).build()
        daq = _built(
            SimDaqConfig(
                link="c", ports={"t": {"port": "output", "quantity": "temperature", "unit": "°C"}}
            ),
            "thermo",
            plant,
        )
        daq.poll_s = 1.0
        drive = _built(
            SimDriveConfig(
                link="c",
                ports={
                    "t": {
                        "port": "input",
                        "demand": "output",
                        "quantity": "temperature",
                        "unit": "°C",
                    }
                },
            ),
            "heater",
            plant,
        )
        for device in (daq, drive):
            rig.add_device(device)
            rig.start_polling(device)
        controller = rig.attach_controller(
            drive.signals["t"], daq.signals["t"], law=PI(kp=0.02, ki=0.0005)
        )
        assert controller.feedforward.tag == "setpoint", "units agree, so no feedforward was given"
        controller.regulate(60.0)
        clock.advance(600)
        assert rig.latest[daq.signals["t"]].value == pytest.approx(60.0, abs=0.5)


def test_a_plant_s_model_field_is_model_not_kind():
    with pytest.raises(ValidationError, match="kind"):
        PlantConfig.model_validate({"kind": "lag"})
    assert PlantConfig.model_validate({"model": "fopdt", "dead_s": 2}).model == "fopdt"


def test_a_bare_plant_needs_its_quantity_spelled_out():
    plant = PlantConfig(model="lag", initial=5.0)
    with pytest.raises(ValueError, match="say `\\{port, quantity, unit\\}`"):
        SimDaqConfig(link=plant, ports={"t": "output"}).build("tc")
    with pytest.raises(ValueError, match="both `quantity` and `unit`"):
        SimDaqConfig(link=plant, ports={"t": DaqPort(port="output", unit="°C")}).build("tc")
    with pytest.raises(ValueError, match="no output port 'zone1'"):
        SimDaqConfig(link=plant, ports={"t": DaqPort(port="zone1")}).build("tc")
    daq = SimDaqConfig(
        link=plant,
        ports={"t": {"port": "output", "quantity": "temperature", "unit": "°C", "range": [0, 9]}},
    ).build("tc")
    assert isinstance(daq.plant, Noisy)
    t = daq.signals["t"]
    assert t.unit.symbol == "°C" and t.spec.range == (0.0, 9.0) and t.access == Access.RP
    (sample,) = daq.read(0)
    assert sample.values[t] == 5.0
    with pytest.raises(ValueError, match="at least one port"):
        SimDaqConfig(link=plant, ports={}).build("tc")


class TestABarePlantIsSteppedOncePerInstant:
    """Two readers of one bare plant must not each step it: time would run at twice its rate."""

    PORTS = {"t": {"port": "output", "quantity": "temperature", "unit": "°C"}}

    def _one_second(self, plants) -> float:
        daqs = [
            _built(SimDaqConfig(link="p", ports=self.PORTS), f"d{i}", p)
            for i, p in enumerate(plants)
        ]
        for time_ns in (0, 1_000_000_000):
            for daq in daqs:
                list(daq.read(time_ns))
        return plants[0].output

    def test_one_reader_is_one_time_constant(self):
        assert self._one_second([Lag(1.0, input=1.0)]) == pytest.approx(1 - math.exp(-1))

    def test_two_readers_of_one_plant_still_see_one_time_constant(self):
        lag = Lag(1.0, input=1.0)
        assert self._one_second([lag, lag]) == pytest.approx(1 - math.exp(-1))  # was 0.8647

    def test_shared_through_separate_noise_wrappers(self):
        lag = Lag(1.0, input=1.0)
        self._one_second([Noisy(lag, 0.0), Noisy(lag, 0.0)])
        assert lag.output == pytest.approx(1 - math.exp(-1))

    def test_an_earlier_instant_does_not_step_it_again(self):
        lag = Lag(1.0, input=1.0)
        a, b = (_built(SimDaqConfig(link="p", ports=self.PORTS), n, lag) for n in "ab")
        list(a.read(0))
        list(a.read(1_000_000_000))
        list(b.read(500_000_000))  # b's poll stamped a little before a's
        list(a.read(1_000_000_000))
        assert lag.output == pytest.approx(1 - math.exp(-1))
