"""Small statistics over a channel's recent readings, computed on request, off the process path."""

from __future__ import annotations

from collections.abc import Sequence
from math import sqrt

from flyball.core.reading import Reading

__all__ = ["noise", "rate"]


def noise(readings: Sequence[Reading], window: int = 5) -> float | None:
    """The standard deviation about a short moving mean: what a sensor's noise looks like.

    Detrended with a `window`-point moving mean, so a ramp does not read as
    noise. None with fewer than `window + 2` readings.
    """
    values = [r.value for r in readings]
    if len(values) < window + 2:
        return None
    half = window // 2
    residuals = []
    for i in range(half, len(values) - half):
        local = values[i - half : i + half + 1]
        residuals.append(values[i] - sum(local) / len(local))
    return sqrt(sum(r * r for r in residuals) / len(residuals)) * _bessel(window)


def _bessel(window: int) -> float:
    """A moving mean over `window` points absorbs 1/window of the variance; put it back."""
    return sqrt(window / (window - 1)) if window > 1 else 1.0


def rate(readings: Sequence[Reading], per_s: float = 60.0) -> float | None:
    """The slope of the readings by least squares, per `per_s` seconds (default: per minute).

    None with fewer than three readings or no time span.
    """
    if len(readings) < 3:
        return None
    t0 = readings[0].time_ns
    times = [(r.time_ns - t0) / 1e9 for r in readings]
    values = [r.value for r in readings]
    n = len(times)
    mean_t = sum(times) / n
    mean_v = sum(values) / n
    denominator = sum((t - mean_t) ** 2 for t in times)
    if denominator == 0:
        return None
    slope = (
        sum((t - mean_t) * (v - mean_v) for t, v in zip(times, values, strict=True)) / denominator
    )
    return slope * per_s
