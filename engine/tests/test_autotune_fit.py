"""Settling detection in the autotune fit."""

from __future__ import annotations

import pytest

from flyball.autotune.fit import SteadyState


def test_a_steady_reading_settles_once_the_window_fills():
    steady = SteadyState(window=1.0, band=0.1)
    assert not steady.push(0.0, 1.0) and not steady.push(0.5, 1.02)
    assert steady.push(1.0, 1.0)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_reading_in_the_window_never_settles(bad):
    """A NaN compares false both ways, so max - min skipped it and a dropout looked steady."""
    steady = SteadyState(window=1.0, band=0.1)
    steady.push(0.0, 1.0)
    steady.push(0.5, bad)
    assert not steady.push(1.0, 1.0)
    assert not steady.push(1.4, 1.0), "still in the window"
    assert steady.push(2.0, 1.0), "settles once it has aged out"
