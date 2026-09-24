"""The laws beyond PID, each closing a loop on a simulated plant, and their wire models."""

from __future__ import annotations

from collections.abc import Callable
from math import exp
from typing import Any

import pytest
from flyball_sim.plant import Fopdt, Lag, Noisy

from flyball.autotune.rules import imc
from flyball.autotune.types import FOPDT
from flyball.control import IMC, PI, PID, OnOff, Scheduled, SlidingMode, SmithPredictor
from flyball.control.laws import Weighted
from flyball.model.catalog import Catalogs, get_catalog
from flyball.model.law import ControlLaw


class Loop:
    """A law closing a loop on a plant.

    `demand = setpoint + correction` for a smart drive (the demand in the
    reading's units, `inverse` mapping it to the plant's input), or
    `demand = correction` for a raw one.
    """

    def __init__(
        self,
        law: ControlLaw,
        plant: Noisy | Lag | Fopdt,
        dt: float,
        lo: float,
        hi: float,
        inverse: Callable[[float], float] | None = None,
        smart: bool = True,
    ) -> None:
        self.law, self.plant, self.dt, self.lo, self.hi = law, plant, dt, lo, hi
        self.inverse, self.smart = inverse, smart
        self.elapsed = 0.0
        self.delivered: float | None = None
        self.trace: list[float] = []
        self.demands: list[float] = []

    def run(self, seconds: float, setpoint: float) -> list[float]:
        out = []
        for _ in range(round(seconds / self.dt)):
            reading = self.plant.output
            correction = self.law.update(self.elapsed, reading, setpoint, self.delivered)
            base = setpoint if self.smart else 0.0
            demand = min(max(base + correction, self.lo), self.hi)
            self.delivered = demand - base
            self.plant.input = self.inverse(demand) if self.inverse else demand
            self.plant.advance(self.dt)
            self.elapsed += self.dt
            out.append(self.plant.output)
            self.demands.append(demand)
        self.trace.extend(out)
        return out


def oven_plant(noise: float = 0.05) -> Noisy:
    """`examples/simulated/oven.yaml`: tau_s 60 s, dead 5 s, gain 80 over ambient 20."""
    return Noisy(Fopdt(60.0, 5.0, gain=80.0, value=20.0, ambient=20.0), noise, seed=1)


def oven_loop(law: ControlLaw) -> Loop:
    """The oven under its smart drive: a demand in °C, the drive inverting the plant."""
    return Loop(law, oven_plant(), 1.0, 20.0, 100.0, lambda d: (d - 20.0) / 80.0)


def settled(trace: list[float], target: float, tail: int = 60, within: float = 0.5) -> bool:
    return all(abs(v - target) < within for v in trace[-tail:])


# region The models every law generates


# A per-tag table of kwargs to build each registered law with, rather than a
# fixed list of instances: a new law registered in the catalog and left out
# here fails loudly (below) instead of silently going untested. A tag maps
# to a *list* of kwargs so more than one configuration can be covered --
# IMC appears twice on purpose, once with derivative action and once without.
_LAW_KWARGS: dict[str, list[dict[str, Any]]] = {
    "open_loop": [{}],
    "p": [{"kp": 1.0}],
    "pi": [{"kp": 1.0, "ki": 0.1, "b": 0.7}],
    "pid": [{"kp": 1.0, "ki": 0.1, "kd": 2.0, "b": 0.7}],
    "imc": [
        {"gain": 1.0, "tau_s": 60.0, "dead_time_s": 5.0},
        {"gain": 2.0, "tau_s": 30.0, "lam_s": 10.0, "derivative": False},
    ],
    "on_off": [{"high": 1.0, "low": 0.0, "hysteresis": 0.5}],
    "smith": [{"kp": 1.0, "ki": 0.05, "gain": 1.0, "tau_s": 20.0, "dead_time_s": 20.0}],
    "scheduled": [{"points": [[0, 1, 0.1, 0], [100, 2, 0.2, 1]], "tt_s": 5.0}],
    "sliding": [{"k": 10.0, "lam": 0.05, "boundary": 2.0}],
}


def _each_registered_law() -> list[ControlLaw]:
    # A fresh Catalogs here, not `get_catalog()`: the session catalog is only
    # set by a fixture, which has not run yet when parametrize builds this
    # list at collection time.
    catalog = Catalogs()
    catalog.discover()
    missing = set(catalog.laws) - set(_LAW_KWARGS)
    assert not missing, f"no round-trip kwargs table entry for law(s): {missing}"
    return [cls(**kwargs) for tag, cls in catalog.laws.items() for kwargs in _LAW_KWARGS[tag]]


@pytest.mark.parametrize("law", _each_registered_law(), ids=lambda law: law.type)
def test_each_law_round_trips_through_its_config_and_view(law):
    assert get_catalog().laws[law.type] is type(law)
    rebuilt = law.config.build()
    assert type(rebuilt) is type(law) and rebuilt.config == law.config
    law.update(0.0, 10.0, 12.0)
    law.update(1.0, 10.5, 12.0)
    view = law.view
    again = view.build()
    assert again.config == law.config
    assert again.view.model_dump() == view.model_dump(), "state comes back too"


def test_the_registry_has_the_new_tags():
    assert {"imc", "on_off", "smith", "scheduled", "sliding"} <= set(get_catalog().laws)


# endregion

# region Setpoint weighting


def test_b_one_is_the_law_as_it_was():
    plain, weighted = PI(kp=2.0, ki=0.1), PI(kp=2.0, ki=0.1, b=1.0)
    for t in range(20):
        assert plain.update(t, 10.0 + t, 30.0) == weighted.update(t, 10.0 + t, 30.0)
    assert isinstance(plain, Weighted) and plain.b == 1.0


def test_setpoint_weighting_softens_the_kick_and_settles_the_same():
    gains = imc(FOPDT(1.0, 60.0, 5.0), derivative=False)
    kicks = {}
    for b in (1.0, 0.4):
        loop = oven_loop(PI(kp=gains.kp, ki=gains.ki, tt_s=gains.tt_s, b=b))
        loop.run(900, 50.0)
        before = loop.demands[-1] - 50.0
        loop.run(900, 70.0)
        kicks[b] = loop.demands[-900] - 70.0 - before  # the correction's jump on the step
        assert settled(loop.trace, 70.0), b
    assert kicks[0.4] < 0.6 * kicks[1.0]


# endregion

# region IMC


def test_imc_is_pid_with_the_rule_s_gains():
    model = FOPDT(1.0, 60.0, 5.0)
    law = IMC(gain=1.0, tau_s=60.0, dead_time_s=5.0)
    gains = imc(model)
    assert (law.kp, law.ki, law.kd, law.tt_s) == pytest.approx((
        gains.kp,
        gains.ki,
        gains.kd,
        gains.tt_s,
    ))
    pi = IMC(gain=1.0, tau_s=60.0, dead_time_s=5.0, lam_s=30.0, derivative=False)
    gains = imc(model, lam_s=30.0, derivative=False)
    assert (pi.kp, pi.ki, pi.kd) == pytest.approx((gains.kp, gains.ki, 0.0))
    with pytest.raises(ValueError):
        IMC(gain=0.0, tau_s=60.0)


def test_imc_from_the_oven_s_own_numbers_holds_the_oven():
    loop = oven_loop(IMC(gain=1.0, tau_s=60.0, dead_time_s=5.0, derivative=False))
    loop.run(600, 50.0)
    trace = loop.run(600, 75.0)
    assert max(trace) < 80.0 and settled(trace, 75.0) and trace[180] > 70.0


# endregion

# region On-off


def test_on_off_holds_a_band_around_the_setpoint():
    law = OnOff(high=1.0, low=0.0, hysteresis=1.0)
    loop = Loop(law, oven_plant(), 1.0, 0.0, 1.0, smart=False)  # the raw heater: 0 or 1
    loop.run(1800, 50.0)
    tail = loop.trace[-600:]
    # Past a band edge the heater stays as it was for the dead time (5 s) plus
    # up to one sample (1 s) before the switch is seen, and a first-order lag
    # turns round the instant its input does. So the reading runs on towards
    # where it was heading, 100 °C on (20 + 80) or 20 °C off, for at most
    # 6 s of the 60 s lag: 51 + (100 - 51)(1 - e^(-6/60)) = 55.66 on top,
    # 49 - (49 - 20)(1 - e^(-6/60)) = 46.24 below (54.92 / 46.68 with no
    # sampling delay: the floor of the overshoot). The noise (σ = 0.05) moves
    # both the reading that trips the switch and the one recorded: 3σ each.
    run_on = 1.0 - exp(-(5.0 + 1.0) / 60.0)
    noise = 6 * 0.05
    assert max(tail) < 51.0 + (100.0 - 51.0) * run_on + noise, "55.96: the dead time's overshoot"
    assert min(tail) > 49.0 - (49.0 - 20.0) * run_on - noise, "45.94: the dead time's undershoot"
    assert set(loop.demands[-600:]) == {0.0, 1.0}, "it switches, never modulates"
    switches = sum(a != b for a, b in zip(loop.demands[-600:], loop.demands[-599:], strict=False))
    assert 2 <= switches <= 60


def test_on_off_hysteresis_and_resume():
    law = OnOff(high=5.0, low=-5.0, hysteresis=1.0)
    assert law.update(0, 10.0, 12.0) == 5.0, "below by more than the band: on"
    assert law.update(1, 11.5, 12.0) == 5.0, "inside the band: held"
    assert law.update(2, 13.5, 12.0) == -5.0, "above by more than the band: off"
    assert law.update(3, 12.5, 12.0) == -5.0, "inside: held off"
    assert law.resume(10.0, 12.0, 4.0) == 5.0 and law.on
    assert law.resume(10.0, 12.0, -1.0) == -5.0 and not law.on
    with pytest.raises(ValueError):
        OnOff(high=1.0, hysteresis=-1.0)


# endregion

# region Smith predictor


def slow_pipe() -> Noisy:
    """A lag of 20 s behind 20 s of dead time: the plant a plain PI cannot be tuned tight on."""
    return Noisy(Fopdt(20.0, 20.0, gain=1.0, value=0.0, ambient=0.0), 0.01, seed=3)


def test_the_predictor_lets_a_pi_tuned_for_the_lag_alone_hold_a_long_dead_time():
    # tuned as if there were no delay
    fast = imc(FOPDT(1.0, 20.0, 0.0), lam_s=10.0, derivative=False)
    plain = Loop(PI(kp=fast.kp, ki=fast.ki, tt_s=fast.tt_s), slow_pipe(), 1.0, -100, 100)
    plain.run(600, 10.0)
    with_model = Loop(
        SmithPredictor(
            kp=fast.kp, ki=fast.ki, gain=1.0, tau_s=20.0, dead_time_s=20.0, tt_s=fast.tt_s
        ),
        slow_pipe(),
        1.0,
        -100,
        100,
    )
    with_model.run(600, 10.0)
    # With the model exact, the loop is the delay-free one 20 s late. That one,
    # the lag under the setpoint feedforward plus the PI (kp 2, ki 0.1), has
    # the error obey 20e'' + (1 + kp)e' + ki e = 0: roots -0.05 and -0.1, so
    # e(t) = -10e^(-t/20) + 20e^(-t/10) from e(0) = 10, e'(0) = -(1 + kp)10/20,
    # least at e^(-t/20) = 1/4: -1.25. It overshoots to 11.25 (sampling at
    # 1 s rounds it off to 11.11), plus 3σ of noise.
    assert max(with_model.trace) < 10.0 + 1.25 + 3 * 0.01
    assert settled(with_model.trace, 10.0, within=0.2)
    assert max(plain.trace) > 12.0 or not settled(plain.trace, 10.0, within=0.2), (
        "the same gains without the predictor ring or overshoot"
    )
    assert with_model.law.predicted_delayed == pytest.approx(with_model.law.predicted, abs=0.05)


def test_the_predictor_resumes_at_the_correction_in_force():
    law = SmithPredictor(kp=1.0, ki=0.05, gain=1.0, tau_s=20.0, dead_time_s=20.0)
    assert law.resume(10.0, 10.0, 3.0) == pytest.approx(3.0)
    assert law.predicted == law.predicted_delayed == pytest.approx(13.0), "setpoint's share + 3"
    assert law.update(0.0, 10.0, 10.0) == pytest.approx(3.0), "the next step reproduces it"


def test_the_predictor_s_model_is_driven_by_what_was_actually_applied():
    """ENG-12.

    A clamp downstream must not leave the model believing its own unclamped
    output reached the plant -- `last_applied` (what the target actually
    delivered last tick) drives the model when it is given, overriding the
    law's own last output.
    """
    law = SmithPredictor(kp=1.0, ki=0.0, gain=2.0, tau_s=10.0, dead_time_s=0.0, feedforward=0.0)
    # dt is 0 on the very first step: the model does not move yet.
    output = law.update(0.0, 0.0, 10.0)
    assert output == pytest.approx(10.0)  # kp * error, no integral
    assert law.predicted == 0.0

    # The next tick reports that only 1.0 of that 10.0 was actually applied
    # (e.g. the target clamped it). The model must move as if 1.0 drove it,
    # not the 10.0 the law itself returned last step.
    law.update(1.0, 0.0, 10.0, last_applied=1.0)
    target = law.gain * (law.feedforward * 10.0 + 1.0)  # 2.0
    assert law.predicted == pytest.approx(target + (0.0 - target) * exp(-1.0 / law.tau_s))
    # Under the old last-output-driven model this would instead have moved
    # towards gain * 10.0 = 20.0, a very different (and wrong) target.
    assert law.predicted < 1.0


# endregion

# region Gain scheduling


def test_scheduled_gains_interpolate_and_hold_at_the_ends():
    law = Scheduled(points=[[100, 2, 0.2, 0], [0, 1, 0.1, 0]])  # any order
    assert law.gains_at(-10) == (1.0, 0.1, 0.0) and law.gains_at(500) == (2.0, 0.2, 0.0)
    assert law.gains_at(50) == pytest.approx((1.5, 0.15, 0.0))
    law.update(0.0, 40.0, 50.0)
    assert (law.kp_now, law.ki_now) == pytest.approx((1.5, 0.15))
    with pytest.raises(ValueError, match="share"):
        Scheduled(points=[[0, 1, 1, 0], [0, 2, 2, 0]])


def test_a_schedule_change_is_bumpless():
    law = Scheduled(points=[[0, 1, 0.1, 0], [100, 1, 0.4, 0]])
    for t in range(1, 60):
        law.update(float(t), 20.0, 30.0)  # a steady error at setpoint 30: the integral fills
    out_before = law.update(60.0, 20.0, 30.0)
    held = law.integral_value
    out_after = law.update(60.0, 20.0, 90.0)  # the setpoint jumps: ki goes 0.19 -> 0.37
    assert law.ki_now > 0.3
    assert law.integral_value == pytest.approx(held, rel=1e-6), "the integral's contribution held"
    assert out_after - out_before == pytest.approx(law.kp_now * 60.0), (
        "only the proportional term moved"
    )


def test_a_scheduled_oven_is_tighter_at_both_ends_than_one_tuning():
    hot = imc(FOPDT(1.0, 60.0, 5.0), lam_s=20.0, derivative=False)
    loop = oven_loop(
        Scheduled(points=[[30, hot.kp, hot.ki, 0], [90, hot.kp * 0.6, hot.ki * 0.6, 0]])
    )
    loop.run(900, 40.0)
    assert settled(loop.trace, 40.0)
    loop.run(900, 85.0)
    assert settled(loop.trace, 85.0)


# endregion

# region Sliding mode


def test_sliding_mode_reaches_the_surface_and_stays_within_k():
    law = SlidingMode(k=30.0, lam=1 / 30.0, boundary=3.0)
    loop = oven_loop(law)
    trace = loop.run(900, 60.0)
    assert settled(trace, 60.0, within=0.6)
    assert max(trace) < 63.0
    corrections = [d - 60.0 for d in loop.demands]
    assert max(abs(c) for c in corrections) <= 30.0 + 1e-9, "never more than k"
    # A load change: the room warms (the drive's inverse is now off by 5 °C); it still holds.
    loop.inverse = lambda d: (d - 15.0) / 80.0
    trace = loop.run(900, 60.0)
    assert settled(trace, 60.0, within=0.6)


def test_sliding_mode_resume_reproduces_the_correction_within_k():
    law = SlidingMode(k=10.0, lam=0.1, boundary=2.0)
    assert law.resume(50.0, 50.0, 4.0) == pytest.approx(4.0)
    assert law.update(0.0, 50.0, 50.0) == pytest.approx(4.0)
    assert law.resume(50.0, 50.0, 25.0) == 10.0, "clipped to k: the bump is reported"
    with pytest.raises(ValueError):
        SlidingMode(k=1.0, lam=1.0, boundary=0.0)


# endregion

# region A step back in time (ENG-11)


def test_step_integral_skips_a_step_back_in_time():
    law = PI(kp=1.0, ki=1.0)
    law.update(0.0, 10.0, 12.0)  # error 2, dt 0: adds nothing, integral 0
    law.update(1.0, 10.0, 12.0)  # error 2, dt 1: integral -> 2
    before = law.integral
    out_of_order = law.update(0.5, 10.0, 12.0)  # elapsed goes backwards: skipped
    assert law.integral == before, "a step back in time adds nothing"
    assert out_of_order == pytest.approx(law.proportional(1.0, 10.0, 12.0) + before)
    # last_elapsed was left where it was; the next forward step sees dt from there.
    law.update(2.0, 10.0, 12.0)
    assert law.integral == pytest.approx(before + 2.0 * 1.0)


def test_sliding_mode_skips_a_step_back_in_time():
    # error 0.5, well inside the boundary layer, so the integral is running.
    law = SlidingMode(k=10.0, lam=0.1, boundary=2.0)
    law.update(0.0, 9.5, 10.0)
    law.update(1.0, 9.5, 10.0)
    before = law.integral
    law.update(0.5, 9.5, 10.0)  # elapsed goes backwards: skipped
    assert law.integral == before, "a step back in time adds nothing to the surface's integral"
    law.update(2.0, 9.5, 10.0)
    assert law.integral != before, "a forward step still runs normally afterwards"


# endregion

# region Derivative filter (ENG-13)


def test_n_omitted_leaves_the_derivative_exactly_as_before():
    """Proves ENG-13 is a no-op when `n` is not set: the raw, unfiltered rate."""
    law = PID(kp=0.0, ki=0.0, kd=2.0)
    assert law.n is None
    law.update(0.0, 10.0, 12.0)
    output = law.update(1.0, 8.0, 12.0)  # reading fell by 2 over 1 s: rate = +2
    assert output == pytest.approx(2.0 * 2.0), "kd * raw rate, no filtering"
    output = law.update(2.0, 6.0, 12.0)  # another sharp step: an unfiltered law reacts fully
    assert output == pytest.approx(2.0 * 2.0)


def test_n_filters_the_derivative_towards_a_first_order_lag():
    law = PID(kp=0.0, ki=0.0, kd=2.0, n=1.0)
    assert law.n == 1.0
    law.update(0.0, 10.0, 12.0)
    first = law.update(1.0, 8.0, 12.0)  # a sharp step in the rate
    raw = 2.0
    alpha = 1.0 / (1.0 + 1.0)  # dt / (dt + 1/n), dt=1, n=1
    expected_rate = alpha * raw
    assert first == pytest.approx(law.kd * expected_rate)
    assert first < 2.0 * raw, "filtered: reacts less than the raw rate would in one step"
    # A steady rate afterwards: the filter catches up.
    second = law.update(2.0, 6.0, 12.0)
    assert second == pytest.approx(2.0 * 2.0, rel=0.4)


def test_n_must_be_positive():
    with pytest.raises(ValueError):
        PID(kp=1.0, kd=1.0, n=0.0)
    with pytest.raises(ValueError):
        PID(kp=1.0, kd=1.0, n=-1.0)


def test_imc_and_scheduled_pass_n_through_to_the_pid():
    imc_law = IMC(gain=1.0, tau_s=60.0, dead_time_s=5.0, n=5.0)
    assert imc_law.n == 5.0
    scheduled_law = Scheduled(points=[[0, 1, 0.1, 0], [100, 2, 0.2, 1]], n=5.0)
    assert scheduled_law.n == 5.0


# endregion
