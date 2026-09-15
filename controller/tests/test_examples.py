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
