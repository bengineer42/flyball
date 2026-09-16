"""The simulated example rigs: they load, run on a stepped clock, and regulate."""

from __future__ import annotations

from pathlib import Path

import pytest

from flyball.runtime.config import load_rig_config
from flyball.sim import Fopdt, Integrator, Lag, Noisy, SteppedClock

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"


def run(name: str, setpoint: float, seconds: float) -> tuple[float, list[float]]:
    """Regulate the example's only loop on a stepped clock: the final reading and the trace."""
    config = load_rig_config(EXAMPLES / name)
    clock = SteppedClock(0)
    rig = config.build(clock=clock, start=False)
    (reader,) = rig.readers.by_name.values()
    (loop_name,) = rig.loops
    loop = rig.loops[loop_name]
    period = config.readers[0].period_s or 1.0
    rig.read(reader)
    loop.regulate(setpoint)
    trace = []
    for _ in range(int(seconds / period)):
        clock.advance(period)
        rig.read(reader)
        assert loop.reading is not None
        trace.append(loop.reading.value)
    return trace[-1], trace


@pytest.mark.parametrize(
    ("name", "setpoint", "seconds", "tolerance"),
    [("oven.toml", 50.0, 600, 0.5), ("tank.toml", 40.0, 300, 1.0), ("bench.toml", 12.0, 5, 0.1)],
)
def test_example_rigs_settle_at_their_setpoints(name, setpoint, seconds, tolerance):
    final, trace = run(name, setpoint, seconds)
    assert final == pytest.approx(setpoint, abs=tolerance)
    assert max(trace) < setpoint * 1.3, "no gross overshoot"


def test_chiller_settles_while_cooling():
    """Reverse-acting: the setpoint is below the 22 C it starts at.

    "No overshoot" means not undershooting past it, the mirror of the heating
    examples above.
    """
    final, trace = run("chiller.toml", 5.0, 900)
    assert final == pytest.approx(5.0, abs=0.5)
    assert min(trace) > 5.0 - 2.0, "no gross undershoot"


def test_dual_settles_both_loops():
    """`dual.toml` has two independent loops, so the single-reader `run()` helper does not fit.

    Drive both directly and check each settles in its own unit.
    """
    config = load_rig_config(EXAMPLES / "dual.toml")
    clock = SteppedClock(0)
    rig = config.build(clock=clock, start=False)
    readers = list(rig.readers.by_name.values())
    for reader in readers:
        rig.read(reader)
    rig.loops["heater"].regulate(50.0)
    rig.loops["valve"].regulate(40.0)
    for _ in range(1200):
        clock.advance(0.5)
        for reader in readers:
            rig.read(reader)
    assert rig.loops["heater"].reading.value == pytest.approx(50.0, abs=0.5)
    assert rig.loops["valve"].reading.value == pytest.approx(40.0, abs=1.0)


def test_every_example_validates_and_names_a_default_loop():
    for path in EXAMPLES.glob("*.toml"):
        config = load_rig_config(path)
        assert config.name and any(loop.default for loop in config.loops), path.name


class TestPlants:
    def test_lag_rests_at_ambient_and_feedforward_inverts_it(self):
        lag = Lag(tau_s=10, value=20, gain=80, ambient=20)
        lag.drive(0.0, 1000)
        assert lag.output == pytest.approx(20)
        lag.input = lag.feedforward(60)
        lag.step(1000)
        assert lag.output == pytest.approx(60)

    def test_integrator_with_and_without_leak(self):
        tank = Integrator(gain=2.0, leak=0.0, value=10)
        tank.input = 1.0
        assert tank.step(5.0) == pytest.approx(20.0)
        drained = Integrator(gain=2.0, leak=0.1, value=0)
        drained.input = drained.feedforward(40)
        drained.step(1000)
        assert drained.output == pytest.approx(40)

    def test_fopdt_delays_the_input(self):
        plant = Fopdt(tau_s=1.0, dead_s=5.0, gain=1.0)
        plant.input = 1.0
        for _ in range(4):
            plant.step(1.0)
        assert plant.output == pytest.approx(0.0), "nothing arrives inside the dead time"
        for _ in range(20):
            plant.step(1.0)
        assert plant.output == pytest.approx(1.0, abs=1e-6)

    def test_noisy_reads_through_noise_but_steps_the_clean_plant(self):
        clean = Lag(tau_s=1.0, value=5.0)
        noisy = Noisy(clean, sigma=0.5, seed=0)
        readings = [noisy.output for _ in range(50)]
        assert clean.output == 5.0 and any(abs(r - 5.0) > 0.1 for r in readings)
        assert noisy.feedforward(3.0) == clean.feedforward(3.0)
