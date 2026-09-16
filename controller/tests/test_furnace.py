"""The multi-zone furnace, its ports, and the loop commands that run a firing on it."""

from __future__ import annotations

from pathlib import Path

import pytest

from flyball.control.feedforward import NoFeedforward, Table
from flyball.core.reading import Source
from flyball.programmer import Hold, Manual, Program, Programmer, Ramp, Regulate
from flyball.runtime.config import RigConfig, resolve_document
from flyball.sim import Furnace, Port, SteppedClock
from flyball.sim.furnace import KELVIN, STEFAN_BOLTZMANN

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


# The single-zone losses curve for the furnace's shared loss model
# (`[links.tube]`: loss_w_per_k = 1.5, emissivity = 0.8, area_m2 = 0.01,
# ambient_c = 20), independent of which zone: `_losses` takes no zone index.
def _zone_losses(temperature_c: float) -> float:
    kelvin = max(temperature_c + KELVIN, 0.0)
    radiative = 0.8 * STEFAN_BOLTZMANN * 0.01 * (kelvin**4 - (20.0 + KELVIN) ** 4)
    return 1.5 * (temperature_c - 20.0) + radiative


def _fresh_furnace_rig():
    for name in ("zone1", "zone2", "zone3", "sample"):
        Source.forget(name)
    document, _ = resolve_document(EXAMPLES / "furnace.toml")
    document["clock"] = {"stepped": True}
    return RigConfig.model_validate(document).build()


def _run_ramp_to_700(rig, feedforward) -> float:
    """Ramp all three zones to 700 at 15 degC/min, hold 20 min; zone2's peak overshoot."""
    loop = rig.loops["heater2"]
    loop.feedforward = feedforward
    peak = float("-inf")

    def record(_loop, reading):
        nonlocal peak
        if reading is not None:
            peak = max(peak, reading.value)

    loop.attach_on_tick(record)
    programmer = Programmer(rig)
    programmer.start(
        Program([
            Regulate(20, loop=["heater1", "heater2", "heater3"]),
            Ramp(700, pace=Rate_per_minute(15), loop=["heater1", "heater2", "heater3"]),
            Hold(Duration_minutes(20)),
            Manual(loop=["heater1", "heater2", "heater3"]),
        ])
    )
    programmer.join(60)
    assert programmer.running is False, "program did not finish"
    loop.detach_on_tick(record)
    return peak - 700.0


def test_rate_feedforward_beats_plain_pi_which_beats_a_static_table(furnace_rig):
    """The handoff's principled fix, checked: a rate term earns its keep on a ramp.

    Measured on zone 2 (heater2, 6000 W, capacity 3000 J/K): a static `table`
    feedforward of the losses curve alone makes a 15 degC/min ramp to 700 *worse*
    than plain PI (it adds hold power on top of an already wound-up integral,
    as `furnace.toml` warns); adding `rate_gain` -- the extra power to charge
    the zone's thermal mass at the ramp's rate, `capacity_j_per_k` itself
    since the loop hands the feedforward a rate in degC *per second*, not per
    minute -- fixes that and beats plain PI too. Observed overshoot: plain PI
    ~4.0 degC, table alone ~7.6 degC, table + rate_gain ~2.2 degC.
    """
    points = [(t, _zone_losses(t)) for t in (20, *range(100, 1101, 100))]
    plain = _run_ramp_to_700(furnace_rig, NoFeedforward())
    static_table = _run_ramp_to_700(_fresh_furnace_rig(), Table(points))
    rated_table = _run_ramp_to_700(_fresh_furnace_rig(), Table(points, rate_gain=3000.0))
    for name in ("zone1", "zone2", "zone3", "sample"):
        Source.forget(name)  # the last _fresh_furnace_rig(); furnace_rig forgets its own

    assert static_table > plain, "a static table adds to an already wound-up integral"
    assert rated_table < plain - 1.0, "the rate term should clearly beat plain PI"


def Rate_per_minute(value: float):  # noqa: N802  a test helper reading like the file
    from flyball.core.clock import Speed, TimeUnit

    return Speed(value, TimeUnit.MINUTE)


def Duration_minutes(value: float):  # noqa: N802
    from flyball.core.clock import Duration

    return Duration.from_seconds(value * 60)


def test_api_devices_lists_every_reader_and_actuator_once(furnace_rig):
    """`GET /api/devices` names the whole rig, whatever kind each device is."""
    from fastapi.testclient import TestClient

    from flyball.server import create_app, set_rig

    rig = furnace_rig
    set_rig(rig)
    try:
        with TestClient(create_app()) as client:
            devices = client.get("/api/devices").json()
            assert set(devices) == {
                "zone1",
                "zone2",
                "zone3",
                "sample",
                "heater1",
                "heater2",
                "heater3",
            }
            for name in ("zone1", "zone2", "zone3", "sample"):
                assert devices[name]["kind"] == "reader"
                assert devices[name]["type"] == "SimReader"
            for name in ("heater1", "heater2", "heater3"):
                assert devices[name]["kind"] == "actuator"
                assert devices[name]["type"] == "SimActuator"
                # A sim device binds its plant directly (`.plant`, not `.link`), so it
                # names no link here; see test_devices.py for a device that does.
                assert devices[name]["link"] is None

            single = client.get("/api/devices/heater2").json()
            assert single == devices["heater2"]
            assert client.get("/api/devices/nonesuch").status_code == 404
    finally:
        set_rig(None)
