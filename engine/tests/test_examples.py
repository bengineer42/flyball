"""The simulated example rigs: they load, run on a stepped clock, and regulate."""

from __future__ import annotations

from pathlib import Path

import pytest
from flyball_sim import Fopdt, Integrator, Lag, Noisy, SteppedClock

from flyball.runtime.config import load_rig_config

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"
STRESS = Path(__file__).resolve().parents[2] / "examples" / "stress"


def run(name: str, setpoint: float, seconds: float) -> tuple[float, list[float]]:
    """Regulate the example's only controller on a stepped clock: the final reading, the trace."""
    config = load_rig_config(EXAMPLES / name)
    clock = SteppedClock(0)
    rig = config.build(clock=clock, start=False)
    ((_, controller),) = rig.controllers.items()
    source = controller.measured_signal
    period = source.poll_s or 1.0
    rig.read(source, fresh=True)
    controller.regulate(setpoint)
    trace = []
    for _ in range(int(seconds / period)):
        clock.advance(period)
        rig.read(source, fresh=True)
        assert controller.state.measured is not None
        trace.append(controller.state.measured.value)
    return trace[-1], trace


@pytest.mark.parametrize(
    ("name", "setpoint", "seconds", "tolerance"),
    [("oven.yaml", 50.0, 600, 0.5), ("tank.yaml", 40.0, 300, 1.0), ("bench.yaml", 12.0, 5, 0.1)],
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
    final, trace = run("chiller.yaml", 5.0, 900)
    assert final == pytest.approx(5.0, abs=0.5)
    assert min(trace) > 5.0 - 2.0, "no gross undershoot"


def test_dual_settles_both_controllers():
    """`dual.yaml` has two independent controllers, so the single-source `run()` does not fit.

    Drive both directly and check each settles in its own unit.
    """
    config = load_rig_config(EXAMPLES / "dual.yaml")
    clock = SteppedClock(0)
    rig = config.build(clock=clock, start=False)
    sources = [rig.resolve("dual_temp.temperature"), rig.resolve("dual_level.volume")]
    rig.read(sources, fresh=True)
    heater, valve = rig.controllers["heater.drive"], rig.controllers["valve.drive"]
    heater.regulate(50.0)
    valve.regulate(40.0)
    for _ in range(1200):
        clock.advance(0.5)
        rig.read(sources, fresh=True)
    assert heater.state.measured is not None and valve.state.measured is not None
    assert heater.state.measured.value == pytest.approx(50.0, abs=0.5)
    assert valve.state.measured.value == pytest.approx(40.0, abs=1.0)


def test_every_example_validates_builds_and_names_a_default_controller():
    for path in sorted(EXAMPLES.glob("*.yaml")):
        config = load_rig_config(path)
        assert config.name and any(c.default for c in config.controllers.values()), path.name
        rig = config.build(start=False)
        assert set(rig.devices) == set(config.devices), path.name
        assert set(rig.controllers) == set(config.controllers), path.name


def test_every_stress_rig_validates_and_builds():
    """The stress rigs are exercised in `test_stress.py`; here only that the files are whole."""
    for path in sorted(STRESS.glob("*.yaml")):
        config = load_rig_config(path)
        assert config.name, path.name
        rig = config.build(start=False)
        assert set(rig.devices) == set(config.devices), path.name


def test_no_example_is_left_in_the_old_format():
    """Every example rig is YAML in the `devices:`/`controllers:` shape; no `.toml` remains."""
    for directory in (EXAMPLES, STRESS):
        assert not list(directory.glob("*.toml")), directory
        for path in directory.glob("*.yaml"):
            text = path.read_text()
            assert text.startswith("# yaml-language-server: $schema="), path.name
            assert "readers:" not in text and "actuators:" not in text and "loops:" not in text


class TestPlants:
    def test_lag_rests_at_ambient_and_feedforward_inverts_it(self):
        lag = Lag(tau_s=10, value=20, gain=80, ambient=20)
        lag.drive(0.0, 1000)
        assert lag.output == pytest.approx(20)
        lag.input = lag.feedforward(60)
        lag.advance(1000)
        assert lag.output == pytest.approx(60)

    def test_integrator_with_and_without_leak(self):
        tank = Integrator(gain=2.0, leak=0.0, value=10)
        tank.input = 1.0
        assert tank.advance(5.0) == pytest.approx(20.0)
        drained = Integrator(gain=2.0, leak=0.1, value=0)
        drained.input = drained.feedforward(40)
        drained.advance(1000)
        assert drained.output == pytest.approx(40)

    def test_fopdt_delays_the_input(self):
        plant = Fopdt(tau_s=1.0, dead_time_s=5.0, gain=1.0)
        plant.input = 1.0
        for _ in range(4):
            plant.advance(1.0)
        assert plant.output == pytest.approx(0.0), "nothing arrives inside the dead time"
        for _ in range(20):
            plant.advance(1.0)
        assert plant.output == pytest.approx(1.0, abs=1e-6)

    def test_noisy_reads_through_noise_but_steps_the_clean_plant(self):
        clean = Lag(tau_s=1.0, value=5.0)
        noisy = Noisy(clean, sigma=0.5, seed=0)
        readings = [noisy.output for _ in range(50)]
        assert clean.output == 5.0 and any(abs(r - 5.0) > 0.1 for r in readings)
        assert noisy.feedforward(3.0) == clean.feedforward(3.0)
