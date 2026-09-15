"""Recent readings on the rig, and the stats computed from them on request."""

from __future__ import annotations

import random

import pytest

from flyball.core.reading import Reading
from flyball.runtime.rig import RECENT_READINGS
from flyball.runtime.stats import noise, rate
from helpers import sample


def readings(values, period_s=1.0):
    """Readings on no channel: the stats look only at time and value."""
    return [Reading(None, None, round(i * period_s * 1e9), v) for i, v in enumerate(values)]  # type: ignore[arg-type]


def test_noise_is_the_sigma_about_a_moving_mean():
    rng = random.Random(1)
    flat = readings([rng.gauss(0.0, 0.3) for _ in range(200)])
    assert noise(flat) == pytest.approx(0.3, rel=0.2)
    ramp = readings([i * 0.5 + rng.gauss(0.0, 0.3) for i in range(200)])
    assert noise(ramp) == pytest.approx(0.3, rel=0.2), "a ramp is not noise"
    assert noise(readings([1.0, 2.0])) is None


def test_rate_is_the_slope_per_minute():
    assert rate(readings([10 + i * 0.5 for i in range(30)])) == pytest.approx(30.0)
    assert rate(readings([5.0, 5.0, 5.0])) == pytest.approx(0.0)
    assert rate(readings([1.0, 2.0])) is None
    assert rate(readings([1.0, 2.0, 3.0], period_s=0)) is None


def test_the_rig_keeps_the_recent_readings_per_channel(rig, probe, temperature, clock):
    channel = probe[temperature]
    assert rig.recent_readings(channel) == [] and rig.reading(channel) is None
    for i in range(RECENT_READINGS + 10):
        rig.on_read([sample(probe, temperature, float(i), clock.advance(1.0), seq=i + 1)])
    recent = rig.recent_readings(channel)
    assert len(recent) == RECENT_READINGS and recent[-1].value == RECENT_READINGS + 9
    assert recent[0].value == 10.0, "the oldest fell off"
    assert [r.value for r in rig.recent_readings(channel, 3)] == [67.0, 68.0, 69.0]
    assert rig.reading(channel) is recent[-1]
