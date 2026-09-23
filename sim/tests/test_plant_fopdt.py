"""`Fopdt` applies a delayed input at the instant it arrives, so the step size does not matter."""

from __future__ import annotations

import math

import pytest

from flyball_sim import Fopdt

TAU, DEAD = 10.0, 5.0


def _after_a_step(t_s: float, dt_s: float, dead_s: float = DEAD) -> float:
    """The output `t_s` after the input steps 0 -> 1, stepped `dt_s` at a time."""
    plant = Fopdt(TAU, dead_s)
    plant.input = 1.0
    for _ in range(round(t_s / dt_s)):
        plant.step(dt_s)
    return plant.output


def _exact(t_s: float, dead_s: float = DEAD) -> float:
    return 1 - math.exp(-max(0.0, t_s - dead_s) / TAU)


@pytest.mark.parametrize("dt_s", [0.1, 0.5, 1.0, 2.5, 5.0, 10.0])
def test_the_output_does_not_depend_on_the_step_size(dt_s):
    assert _after_a_step(10.0, dt_s) == pytest.approx(_exact(10.0))  # 0.3935
    assert _after_a_step(20.0, dt_s) == pytest.approx(_exact(20.0))


@pytest.mark.parametrize("dt_s", [0.7, 1.0, 2.0])
def test_a_dead_time_between_steps_is_honoured(dt_s):
    t = 7.0 * dt_s * 2  # a whole number of steps for each dt
    assert _after_a_step(t, dt_s, dead_s=3.3) == pytest.approx(_exact(t, 3.3))


def test_nothing_arrives_before_the_dead_time():
    assert _after_a_step(4.0, 1.0) == 0.0
    assert _after_a_step(5.0, 5.0) == 0.0, "arrives at 5 s: nothing has moved yet"


def test_no_dead_time_is_a_plain_lag():
    assert _after_a_step(10.0, 2.0, dead_s=0.0) == pytest.approx(1 - math.exp(-1))


def test_an_input_change_mid_run_is_delayed_whole():
    """On for 3 s, then off: a 3 s pulse arrives at 5 s and leaves at 8 s, at any step size."""
    outputs = []
    for dt_s in (0.5, 1.0, 3.0):
        plant = Fopdt(TAU, DEAD)
        plant.input = 1.0
        for _ in range(round(3.0 / dt_s)):
            plant.step(dt_s)
        plant.input = 0.0
        for _ in range(round(9.0 / dt_s)):
            plant.step(dt_s)
        outputs.append(plant.output)
    exact = (1 - math.exp(-3 / TAU)) * math.exp(-(12 - 8) / TAU)
    assert outputs == pytest.approx([exact] * 3)
