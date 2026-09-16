"""The multi-zone furnace, its ports, and the loop commands that run a firing on it."""

from __future__ import annotations

from pathlib import Path

import pytest

from flyball.core.reading import Source
from flyball.programmer import Hold, Manual, Program, Programmer, Ramp, Regulate
from flyball.runtime.config import RigConfig, resolve_document
from flyball.sim import Furnace, Port, SteppedClock

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"


class TestFurnace:
    def test_rests_at_ambient_and_heats_when_driven(self):
        furnace = Furnace(zones=2, sensor_lag_s=0)
        furnace.step(600)
        assert furnace.temperature == [20.0, 20.0]
        furnace.inputs["heater1"] = 1.0
        furnace.step(600)
        assert furnace.temperature[0] > 100 and 20 < furnace.temperature[1] < furnace.temperature[0]
        assert furnace.output("zone1") == pytest.approx(furnace.temperature[0])

    def test_gain_falls_as_it_gets_hot(self):
        furnace = Furnace()
        cold = furnace.feedforward("heater1", 220) - furnace.feedforward("heater1", 200)
        hot = furnace.feedforward("heater1", 920) - furnace.feedforward("heater1", 900)
        assert hot > 2 * cold, "the same 20 degrees costs more drive at 900 than at 200"
        # A heater cannot cool: the feedforward says so by going negative, so the
        # actuator clamps to zero drive and reports ambient as what it can hold.
        assert furnace.feedforward("heater1", -50) < 0.0
        assert furnace.inverse_feedforward("heater1", 0.0) == pytest.approx(
            furnace.ambient, abs=0.01
        )
        assert furnace.feedforward("heater1", -8000) < furnace.feedforward("heater1", -50) < 0.0, (
            "monotonic below ambient: a wound-up law cannot switch the heater back on"
        )
        assert furnace.inverse_feedforward("heater1", furnace.feedforward("heater1", 500)) == (
            pytest.approx(500, abs=0.01)
        )

    def test_sample_and_sensors_lag(self):
        furnace = Furnace(zones=1, sample_zone=1, sensor_lag_s=10)
        furnace.inputs["heater1"] = 1.0
        furnace.step(60)
        assert furnace.sample < furnace.temperature[0]
        assert furnace.measured[0] < furnace.temperature[0]
        furnace.reset(300)
        assert furnace.output("sample") == 300 and furnace.inputs["heater1"] == 0.0

    def test_advance_steps_once_per_instant(self):
        furnace = Furnace(zones=1, sample_zone=1)
        furnace.inputs["heater1"] = 1.0
        furnace.advance(0)
        furnace.advance(60_000_000_000)
        after_one = furnace.temperature[0]
        furnace.advance(60_000_000_000)  # the same instant again: no second step
        assert furnace.temperature[0] == after_one

    def test_ports_and_bad_names(self):
        furnace = Furnace(zones=2)
        heater = Port(furnace, input="heater2")
        heater.input = 0.5
        assert furnace.inputs["heater2"] == 0.5
        with pytest.raises(ValueError, match="no output"):
            Port(furnace, output="zone9")
        with pytest.raises(ValueError, match="one value per zone"):
            Furnace(zones=2, power_w=[1, 2, 3])


@pytest.fixture
def furnace_rig():
    document, _ = resolve_document(EXAMPLES / "furnace.toml")
    document["clock"] = {"stepped": True}
    for name in ("zone1", "zone2", "zone3", "sample"):
        Source.forget(name)
    rig = RigConfig.model_validate(document).build()
    yield rig
    for name in ("zone1", "zone2", "zone3", "sample"):
        Source.forget(name)


def test_the_example_reads_every_port_from_one_plant(furnace_rig):
    rig = furnace_rig
    clock = rig.clock
    assert isinstance(clock, SteppedClock) and clock.scheduled == 4, "polls are on the clock"
    rig.actuators["heater1"].set_demand(600)
    clock.advance(600)
    zones = {n: rig.readers.by_name[n].state.output for n in ("zone1", "zone2", "zone3")}
    assert zones["zone1"] > zones["zone2"] > zones["zone3"] > 20
    assert rig.readers.by_name["sample"].state.output > 20


def test_a_firing_runs_deterministically_on_the_stepped_clock(furnace_rig):
    rig = furnace_rig
    clock = rig.clock
    start = clock.now_ns()
    clock.advance(1)
    programmer = Programmer(rig)
    programmer.start(
        Program([
            Regulate(20, loop=["heater1", "heater2", "heater3"]),
            Ramp(300, pace=Rate_per_minute(10), loop=["heater1", "heater2", "heater3"]),
            Hold(Duration_minutes(10)),
            Manual(loop=["heater1", "heater2", "heater3"]),
        ])
    )
    programmer.join(30)
    assert programmer.running is False
    elapsed_min = (clock.now_ns() - start) / 60e9
    assert elapsed_min == pytest.approx(28 + 10, abs=0.1), "28 min ramp + 10 min hold, no waiting"
    for name in ("zone1", "zone2", "zone3"):
        assert rig.readers.by_name[name].state.output == pytest.approx(300, abs=50)  # it overshoots
        assert rig.loops[name.replace("zone", "heater")].mode.value == "manual"


def test_a_failed_thermocouple_goes_offline_with_an_event(furnace_rig):
    rig = furnace_rig
    rig.readers.by_name["zone3"].fail()
    rig.clock.advance(2)
    run = rig.readers.run("zone3")
    assert run.running is False and run.conditions[0].kind == "offline"
    assert any(e.kind == "offline" and e.subject == "zone3" for e in rig.recent)
    rig.readers.by_name["zone3"].restore()
    assert rig.readers.by_name["zone3"].state.conditions == ()


def Rate_per_minute(value: float):  # noqa: N802  a test helper reading like the file
    from flyball.core.clock import Speed, TimeUnit

    return Speed(value, TimeUnit.MINUTE)


def Duration_minutes(value: float):  # noqa: N802
    from flyball.core.clock import Duration

    return Duration.from_seconds(value * 60)
