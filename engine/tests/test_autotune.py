"""The autotune package against the simulated plants: measure, model, tune.

The experiments are state machines fed `(time, reading)`, so a test drives one
exactly as a controller would -- push a reading, command the target it returns,
step the plant. What is fitted is checked against what the plant was built
with, which is the only check that matters: everything downstream trusts this
number.

Run open-loop throughout, as `flyball.autotune.experiments` says to: the target
goes to the plant through its own feedforward, so a smart drive (one whose
demand is already in the reading's units) yields a unit gain and a raw one
yields the plant's.
"""

from __future__ import annotations

import pytest
from flyball_sim.plant import Fopdt, Lag, Noisy, Plant

from flyball.autotune import (
    FOPDT,
    ExperimentIncompleteError,
    ExperimentTimeoutError,
    Gains,
    RelayTest,
    ResponseTooSmallError,
    Sample,
    SteadyState,
    StepTest,
    Ultimate,
    amigo,
    fit_fopdt,
    imc,
    tyreus_luyben,
    ziegler_nichols,
)
from flyball.autotune.errors import NoDeadTimeError

DT = 1.0
"""Seconds per reading: the rate a 60 s plant would really be polled at."""


def drive(plant: Plant, target: float, dt: float = DT, feedforward: bool = True) -> float:
    """Command `target` for one interval and return the new reading.

    `feedforward` inverts the plant the way a smart drive does; without it the
    target is the raw input and the fitted gain is the plant's own.
    """
    plant.input = plant.feedforward(target) if feedforward else target
    return plant.step(dt)


def run_step_test(
    test: StepTest,
    plant: Plant,
    dt: float = DT,
    feedforward: bool = True,
    limit: int = 20_000,
) -> FOPDT:
    """Drive `test` to completion against `plant`; return the model it fitted."""
    time = 0.0
    reading = plant.output
    for _ in range(limit):
        target = test.step(time, reading)
        if test.done:
            return test.result
        reading = drive(plant, target, dt, feedforward)
        time += dt
    raise AssertionError(f"step test did not finish in {limit} readings")


def run_relay_test(
    test: RelayTest,
    plant: Plant,
    dt: float = DT,
    feedforward: bool = True,
    limit: int = 20_000,
) -> Ultimate:
    """Drive `test` to completion against `plant`; return the critical point."""
    time = 0.0
    reading = plant.output
    for _ in range(limit):
        target = test.step(time, reading)
        if test.done:
            return test.result
        reading = drive(plant, target, dt, feedforward)
        time += dt
    raise AssertionError(f"relay test did not finish in {limit} readings")


def oven(noise: float = 0.0) -> Plant:
    """`oven.yaml`'s plant: tau 60 s, dead 5 s, gain 80 over ambient 20, resting at 50 C."""
    plant = Fopdt(60.0, 5.0, gain=80.0, value=50.0, ambient=20.0)
    plant.input = plant.feedforward(50.0)
    return Noisy(plant, noise, seed=1) if noise else plant


# region SteadyState


def test_steady_state_is_false_until_the_window_fills():
    steady = SteadyState(window=10.0, band=0.5)
    assert not steady.push(0.0, 20.0)
    assert not steady.push(5.0, 20.0)
    assert steady.push(11.0, 20.0)


def test_a_slow_ramp_does_not_read_as_settled():
    """A range test, not a slope test: the docstring's reason for the choice."""
    steady = SteadyState(window=10.0, band=0.5)
    settled = [steady.push(float(t), 20.0 + 0.1 * t) for t in range(30)]
    assert not any(settled)


def test_the_mean_over_a_settled_window_is_the_plateau():
    steady = SteadyState(window=10.0, band=1.0)
    for t in range(30):
        steady.push(float(t), 50.0 + (0.2 if t % 2 else -0.2))
    assert steady.mean == pytest.approx(50.0, abs=0.05)


# region fit_fopdt


def _response(model: FOPDT, size: float, initial: float, dt: float, span: float) -> list[Sample]:
    """The exact step response of `model`, as samples a logger would have kept."""
    return [
        Sample(t * dt, initial + model.response(t * dt, size)) for t in range(int(span / dt) + 1)
    ]


def test_fit_recovers_a_model_it_generated():
    model = FOPDT(gain=2.0, tau=40.0, dead_time=8.0)
    samples = _response(model, size=5.0, initial=20.0, dt=1.0, span=400.0)
    fitted = fit_fopdt(samples, start=0.0, initial=20.0, final=30.0, size=5.0)
    assert fitted.gain == pytest.approx(2.0, rel=1e-3)
    assert fitted.tau == pytest.approx(40.0, rel=0.02)
    assert fitted.dead_time == pytest.approx(8.0, abs=0.5)
    assert fitted.error < 0.05


def test_fit_recovers_a_negative_gain():
    """A chiller pulls the reading down; normalising by the change handles the sign."""
    model = FOPDT(gain=-1.5, tau=30.0, dead_time=4.0)
    samples = _response(model, size=4.0, initial=20.0, dt=1.0, span=300.0)
    fitted = fit_fopdt(samples, start=0.0, initial=20.0, final=14.0, size=4.0)
    assert fitted.gain == pytest.approx(-1.5, rel=1e-3)
    assert fitted.tau == pytest.approx(30.0, rel=0.02)
    assert fitted.dead_time == pytest.approx(4.0, abs=0.5)


def test_fit_refuses_a_reading_that_never_moved():
    samples = [Sample(float(t), 20.0) for t in range(50)]
    with pytest.raises(ResponseTooSmallError):
        fit_fopdt(samples, start=0.0, initial=20.0, final=20.0, size=5.0)


def test_normalised_dead_time_grades_the_plant():
    assert FOPDT(1.0, tau=90.0, dead_time=10.0).normalised_dead_time == pytest.approx(0.1)
    assert FOPDT(1.0, tau=10.0, dead_time=90.0).normalised_dead_time == pytest.approx(0.9)


# region StepTest


def test_step_test_fits_the_plant_it_was_run_against():
    """The round trip that everything else depends on: known plant in, same plant out."""
    plant = oven()
    test = StepTest(base=50.0, size=10.0, window=40.0, band=0.05, timeout=4000.0)
    model = run_step_test(test, plant)
    # A smart drive inverts the plant, so the loop sees unit gain.
    assert model.gain == pytest.approx(1.0, rel=0.05)
    assert model.tau == pytest.approx(60.0, rel=0.1)
    assert model.dead_time == pytest.approx(5.0, abs=2.0)


def test_a_raw_drive_measures_the_plant_gain_itself():
    """Without the feedforward the target is the raw input, so the gain is the plant's 80."""
    plant = Fopdt(60.0, 5.0, gain=80.0, value=20.0, ambient=20.0)
    test = StepTest(base=0.0, size=0.25, window=40.0, band=0.05, timeout=4000.0)
    model = run_step_test(test, plant, feedforward=False)
    assert model.gain == pytest.approx(80.0, rel=0.05)
    assert model.tau == pytest.approx(60.0, rel=0.1)


def test_a_step_test_survives_sensor_noise():
    plant = oven(noise=0.05)
    test = StepTest(base=50.0, size=10.0, window=60.0, band=0.4, timeout=6000.0)
    model = run_step_test(test, plant)
    assert model.gain == pytest.approx(1.0, rel=0.1)
    assert model.tau == pytest.approx(60.0, rel=0.25)


def test_a_step_test_works_on_a_plant_with_no_dead_time():
    plant = Lag(30.0, value=50.0, gain=80.0, ambient=20.0)
    plant.input = plant.feedforward(50.0)
    test = StepTest(base=50.0, size=10.0, window=30.0, band=0.05, timeout=4000.0)
    model = run_step_test(test, plant)
    assert model.tau == pytest.approx(30.0, rel=0.15)
    assert model.dead_time < 2.0


def test_the_target_is_the_base_until_the_first_plateau_is_reached():
    test = StepTest(base=50.0, size=10.0, window=40.0, band=0.05, timeout=4000.0)
    assert test.target == 50.0
    assert not test.stepped
    for t in range(60):
        test.step(float(t), 50.0)
    assert test.stepped
    assert test.target == 60.0


def test_the_result_is_refused_before_the_experiment_finishes():
    test = StepTest(base=50.0, size=10.0, window=40.0, band=0.05)
    with pytest.raises(ExperimentIncompleteError):
        _ = test.result


def test_a_plateau_that_never_arrives_times_out():
    """A reading that will not settle must end the experiment, not run for ever."""
    test = StepTest(base=50.0, size=10.0, window=40.0, band=0.05, timeout=100.0)
    with pytest.raises(ExperimentTimeoutError):
        for t in range(500):
            test.step(float(t), 50.0 + t)  # ramping away: never settles


def test_the_dead_time_plateau_is_not_mistaken_for_the_response():
    """The reading sits at `initial` for theta seconds after the step.

    With a window shorter than the dead time that stillness would otherwise
    settle on the spot and fit a zero response; `experiments.py` guards it by
    requiring the new plateau to differ from the old.
    """
    plant = Fopdt(60.0, 40.0, gain=80.0, value=50.0, ambient=20.0)
    plant.input = plant.feedforward(50.0)
    test = StepTest(base=50.0, size=10.0, window=20.0, band=0.05, timeout=6000.0)
    model = run_step_test(test, plant)
    assert model.dead_time == pytest.approx(40.0, abs=5.0)
    assert model.gain == pytest.approx(1.0, rel=0.1)


# region RelayTest


def test_a_relay_test_finds_a_credible_critical_point():
    plant = oven()
    test = RelayTest(centre=50.0, amplitude=5.0, hysteresis=0.1, cycles=4, timeout=6000.0)
    ultimate = run_relay_test(test, plant)
    assert ultimate.gain > 0.0
    # The limit cycle of a lag-dominated plant runs at a few times the dead time.
    assert 10.0 < ultimate.period < 400.0


def test_the_relay_alternates_about_the_centre():
    test = RelayTest(centre=50.0, amplitude=5.0, hysteresis=0.5)
    assert test.step(0.0, 49.0) == 55.0  # below the band: drive up
    assert test.step(1.0, 51.0) == 45.0  # above it: drive down


def test_a_relay_result_is_refused_before_enough_cycles():
    test = RelayTest(centre=50.0, amplitude=5.0)
    with pytest.raises(ExperimentIncompleteError):
        _ = test.result


def test_a_relay_that_never_swings_times_out():
    test = RelayTest(centre=50.0, amplitude=5.0, hysteresis=0.5, timeout=50.0)
    with pytest.raises(ExperimentTimeoutError):
        for t in range(500):
            test.step(float(t), 50.0)


# region Rules


def test_imc_follows_its_published_form():
    model = FOPDT(gain=2.0, tau=40.0, dead_time=10.0)
    gains = imc(model, lam=40.0, derivative=False)
    # PI: kp = tau / (gain * (lam + theta)), ti = tau.
    assert gains.kp == pytest.approx(40.0 / (2.0 * 50.0))
    assert gains.ti == pytest.approx(40.0)
    assert gains.kd == 0.0


def test_imc_defaults_lambda_to_about_the_plant_speed():
    model = FOPDT(gain=1.0, tau=60.0, dead_time=5.0)
    assert imc(model).kp == pytest.approx(imc(model, lam=60.0).kp)


def test_a_smaller_lambda_asks_for_more_gain():
    model = FOPDT(gain=1.0, tau=60.0, dead_time=5.0)
    assert imc(model, lam=10.0).kp > imc(model, lam=120.0).kp


def test_amigo_needs_a_dead_time():
    with pytest.raises(NoDeadTimeError):
        amigo(FOPDT(gain=1.0, tau=60.0, dead_time=0.0))


def test_tyreus_luyben_is_the_detuned_ziegler_nichols():
    """About half the gain and twice the integral time, as the docstring says."""
    ultimate = Ultimate(gain=4.0, period=20.0)
    zn, tl = ziegler_nichols(ultimate), tyreus_luyben(ultimate)
    assert tl.kp < zn.kp
    assert tl.ti > zn.ti


def test_gains_convert_between_ideal_and_parallel_form():
    gains = Gains.of_ideal(kp=2.0, ti=50.0, td=5.0)
    assert gains.ki == pytest.approx(2.0 / 50.0)
    assert gains.kd == pytest.approx(10.0)
    assert gains.ti == pytest.approx(50.0)
    assert gains.td == pytest.approx(5.0)


def test_gains_choose_pid_or_pi_by_whether_there_is_derivative_action():
    assert Gains.of_ideal(kp=1.0, ti=10.0, td=2.0).config.tag == "PID"
    assert Gains.of_ideal(kp=1.0, ti=10.0).config.tag == "PI"


def test_a_fitted_plant_tunes_to_gains_that_hold_the_loop():
    """End to end: measure a plant, tune from the model, and the loop must settle.

    The point is not the exact gains but that the whole chain produces a
    stable loop on the plant it was measured on.
    """
    plant = oven()
    model = run_step_test(
        StepTest(base=50.0, size=10.0, window=40.0, band=0.05, timeout=4000.0), plant
    )
    gains = imc(model, derivative=False)
    reading, integral, setpoint = plant.output, 0.0, 60.0
    for _ in range(2000):
        error = setpoint - reading
        integral += gains.ki * error * DT
        reading = drive(plant, setpoint + gains.kp * error + integral)
    assert reading == pytest.approx(setpoint, abs=0.5)


def test_to_tuning_names_the_law_for_the_rig():
    tuning = imc(FOPDT(gain=1.0, tau=60.0, dead_time=5.0)).to_tuning("fitted")
    assert tuning.tag == "fitted"
    assert tuning.build() is not None
