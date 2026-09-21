"""Recent readings on the rig, and the stats computed from them on request."""

from __future__ import annotations

import random

import pytest

from flyball.foundation.device import Reading, Sample
from flyball.foundation.router import RECENT_READINGS
from flyball.runtime.stats import noise, rate
from test_rig_devices import Furnace


def readings(values, period_s=1.0):
    """Readings on no signal: the stats look only at time and value."""
    return [Reading(None, round(i * period_s * 1e9), v) for i, v in enumerate(values)]  # type: ignore[arg-type]


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


def test_the_rig_keeps_the_recent_readings_per_signal(rig, clock, fresh):
    furnace = Furnace(fresh("furnace"))
    rig.add_device(furnace)
    zone1 = furnace.signals["zone1"]
    assert rig.recent_readings(zone1) == [] and zone1 not in rig.latest
    for i in range(RECENT_READINGS + 10):
        rig.on_samples([Sample(furnace.root, clock.advance(1.0), {zone1: float(i)})])
    recent = rig.recent_readings(zone1)
    assert len(recent) == RECENT_READINGS and recent[-1].value == RECENT_READINGS + 9
    assert recent[0].value == 10.0, "the oldest fell off"
    assert [r.value for r in rig.recent_readings(zone1, 3)] == [67.0, 68.0, 69.0]
    assert rig.latest[zone1] is recent[-1]
