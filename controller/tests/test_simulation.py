"""Clocks that are not wall time, and the knobs on a simulated rig."""

from __future__ import annotations

import time
from pathlib import Path
from threading import Event

import pytest

from flyball.core.errors import ConflictError, NotFoundError
from flyball.core.reading import Source
from flyball.runtime.config import RigConfig, load_rig_config, resolve_document
from flyball.runtime.simulation import Simulation
from flyball.sim import ScaledClock, SteppedClock

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"


class TestScaledClock:
    def test_runs_at_its_speed_and_waits_proportionally(self):
        clock = ScaledClock(100)
        start = clock.now_ns()
        clock.sleep(1.0)  # one clock second: 10 ms real
        assert 0.9e9 <= clock.now_ns() - start <= 5e9
        t = time.monotonic()
        assert clock.wait(Event(), timeout=2.0) is False
        assert time.monotonic() - t < 0.5

    def test_speed_change_keeps_time_continuous(self):
        clock = ScaledClock(1000)
        clock.sleep(0.5)
        before = clock.now_ns()
        clock.set_speed(1)
        assert clock.now_ns() - before < 0.1e9, "no jump at the change"
        assert clock.speed == 1.0
        with pytest.raises(ValueError):
            clock.set_speed(0)

    def test_elapsed_and_tags_are_in_clock_time(self):
        clock = ScaledClock(50)
        clock.tag("run")
        clock.sleep(0.5)
        assert clock.elapsed_s("run") >= 0.5 and clock.elapsed_s() >= 0.5


class TestSteppedClock:
    def test_sleep_and_timed_wait_advance_instead_of_blocking(self):
        clock = SteppedClock(0)
        clock.sleep(30)
        assert clock.now_ns() == 30_000_000_000
        assert clock.wait(Event(), timeout=10) is False and clock.now_ns() == 40_000_000_000
        set_event = Event()
        set_event.set()
        assert clock.wait(set_event, timeout=10) is True and clock.now_ns() == 40_000_000_000

    def test_a_program_s_timed_wait_passes_at_once(self, rig, clock):
        from flyball.core.clock import Duration
        from flyball.programmer import Program, Programmer, Wait

        programmer = Programmer(rig)
        programmer.start(Program([Wait("hold", timeout=Duration(600))]))
        programmer.join(2)
        assert programmer.running is False and clock.now_ns() >= 600_000_000_000


@pytest.fixture
def oven():
    path = EXAMPLES / "oven.toml"
    document, _ = resolve_document(path)
    config = load_rig_config(path)
    Source.forget("thermocouple")
    rig = config.build(start=False)
    yield Simulation(rig, config, document, path)
    Source.forget("thermocouple")


class TestSimulation:
    def test_a_simulated_rig_gets_a_scaled_clock_by_default(self, oven):
        assert isinstance(oven.rig.clock, ScaledClock) and oven.speed == 1.0
        assert oven.set_speed(60) == 60.0 and oven.config.clock is not None
        assert "clock" in oven.describe()["changed"]

    def test_the_file_may_ask_for_a_speed_or_a_stepped_clock(self):
        base = {"links": {"p": {"tag": "sim_plant"}}}
        assert isinstance(
            RigConfig.model_validate({**base, "clock": {"speed": 10}}).build(start=False).clock,
            ScaledClock,
        )
        rig = RigConfig.model_validate({**base, "clock": {"stepped": True}}).build(start=False)
        assert isinstance(rig.clock, SteppedClock)
        with pytest.raises(ValueError, match="sim_\\* or fake_\\*"):
            RigConfig.model_validate({
                "links": {"v": {"tag": "visa", "resource": "x"}},
                "clock": {"speed": 2},
            })

    def test_hardware_is_not_a_simulation(self):
        from flyball.runtime.rig import Rig

        config = RigConfig.model_validate({"links": {"v": {"tag": "visa", "resource": "x"}}})
        assert config.simulated is False
        with pytest.raises(ConflictError, match="real hardware"):
            Simulation(Rig(), config)

    def test_plants_retune_live_and_keep_their_state(self, oven):
        (name,) = oven.plants
        oven.reset_plant(name, output=50.0)
        updated = oven.set_plant(name, tau_s=30, noise=0.0)
        assert updated.tau_s == 30 and oven.plant_config(name).tau_s == 30
        assert oven.plant_state(name)["output"] == pytest.approx(50.0), "state survives a retune"
        plant = oven.plants[name]
        plant.step(30)  # one time constant with no input: 63 % of the way to ambient (20)
        assert plant.output == pytest.approx(50 - 30 * (1 - 2.718281828**-1), rel=0.02)
        with pytest.raises(ValueError, match="kind"):
            oven.set_plant(name, kind="lag")
        with pytest.raises(ValueError):
            oven.set_plant(name, tau_s=-1)
        with pytest.raises(NotFoundError):
            oven.set_plant("ghost", tau_s=1)

    def test_save_writes_the_file_with_the_changes_and_it_reloads(self, oven, tmp_path):
        (name,) = oven.plants
        oven.set_speed(30)
        oven.set_plant(name, gain=40.0)
        for suffix in (".toml", ".yaml", ".json"):
            out = oven.save(tmp_path / f"oven{suffix}")
            assert out.exists() and oven.describe()["changed"] == []
            Source.forget("thermocouple")
            again = load_rig_config(out)
            assert again.clock is not None and again.clock.speed == 30
            assert again.links[name].gain == 40.0 and again.links[name].dead_s == 5.0
            assert again.readers[0].device.name == "thermocouple", "untouched entries survive"
        assert 'tag = "sim_plant"' in (tmp_path / "oven.toml").read_text()

    def test_stepping_needs_a_stepped_clock(self, oven):
        with pytest.raises(ConflictError, match="not stepped"):
            oven.step(1.0)


class TestLiveValues:
    def test_readings_and_live_links_pair_config_with_what_is_read(self):
        from flyball.runtime.config import RigConfig, resolve_document

        path = EXAMPLES / "furnace.toml"
        document, _ = resolve_document(path)
        document["clock"] = {"stepped": True}
        for name in ("zone1", "zone2", "zone3", "sample"):
            Source.forget(name)
        config = RigConfig.model_validate(document)
        rig = config.build()
        try:
            sim = Simulation(rig, config, document, path)
            rig.actuators["heater1"].set_demand(400)
            rig.clock.advance(120)
            described = sim.describe()
            plant = described["plants"]["tube"]
            readings = plant["readings"]
            assert set(readings) == {"zone1", "zone2", "zone3", "sample"}
            zone1 = readings["zone1"]
            assert zone1["unit"] == "°C" and zone1["reader"] == "zone1" and zone1["precision"] == 2
            assert 0 <= zone1["age_s"] < 10
            stats = plant["stats"]
            assert stats["rate_per_min"]["zone1"] > 5 > stats["rate_per_min"]["zone3"]
            assert stats["noise"]["zone2"] == pytest.approx(0.3, rel=0.5), "configured 0.3"
            live = plant["live"]
            assert set(live) == {"ambient_c", "initial_c", "noise"}
            assert live["ambient_c"] == live["initial_c"] == plant["outputs"]
            assert live["noise"] == stats["noise"] and set(live["noise"]) == set(readings)
            assert plant["links"] == {
                "ambient_c": "outputs.*",
                "initial_c": "outputs.*",
                "noise": "stats.noise",
            }
            assert "tau_s" not in live and "coupling_w_per_k" not in live, "parameters, not states"
            schema = type(config.links["tube"]).model_json_schema()["properties"]
            assert schema["noise"]["live"] == "stats.noise" and "live" not in schema["zones"]
            assert set(described["clock"]) == {"speed", "measured", "stepped", "now_ns"}
        finally:
            for name in ("zone1", "zone2", "zone3", "sample"):
                Source.forget(name)

    def test_single_port_plant_links_to_output(self, oven):
        (name,) = oven.plants
        reader = oven.rig.readers.by_name["thermocouple"]
        for _ in range(3):
            oven.rig.read(reader)
        plant = oven.plant_description(name)
        live = plant["live"]
        assert live["ambient"] == live["initial"] == plant["output"]
        assert set(plant["readings"]["output"]) == {"value", "unit", "precision", "reader", "age_s"}
        assert "noise" not in live, "too few readings for a statistic: left out, not None"

    def test_a_live_path_walks_the_description_and_fans_out_on_a_star(self):
        from flyball.runtime.simulation import resolve_live

        root = {
            "output": 1.5,
            "outputs": {"zone1": 10.0, "zone2": 20.0},
            "readings": {"zone1": {"value": 10.1}, "zone2": {"unit": "°C"}},
            "stats": {"noise": {"zone1": 0.3}},
        }
        assert resolve_live("output", root) == 1.5
        assert resolve_live("outputs.*", root) == {"zone1": 10.0, "zone2": 20.0}
        assert resolve_live("outputs.zone2", root) == 20.0
        assert resolve_live("readings.*.value", root) == {"zone1": 10.1}, "misses are dropped"
        assert resolve_live("stats.noise", root) == {"zone1": 0.3}
        assert resolve_live("stats.rate_per_min", root) is None
        assert resolve_live("output.deeper", root) is None

    def test_measured_speed_needs_a_moment(self, oven):
        assert oven.measured_speed() is None
        oven.rig.clock.set_speed(50)
        time.sleep(0.15)
        assert oven.measured_speed() == pytest.approx(50, rel=0.2)
