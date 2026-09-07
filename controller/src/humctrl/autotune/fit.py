"""Deciding when a reading has settled, and fitting a plant model to a step.

Stdlib only, like the rest of the core: the fit is Smith's two-point estimate
seeding a shrinking grid search on (τ, θ), which is a handful of lines and needs
no optimiser. The gain is not searched over — it comes from the two plateaus the
settling detector already measured, which is a better estimate than anything the
transient can offer.
"""

from __future__ import annotations

from collections import deque
from math import exp
from statistics import fmean

from humctrl.typing import Positive

from .errors import ResponseTooSmallError
from .types import FOPDT, Sample

_LOW = 0.283
"""Smith's lower point: the 28.3% fraction of the total change."""

_HIGH = 0.632
"""Smith's upper point: the 63.2% fraction, one time constant after the delay."""


class SteadyState:
    """A rolling window that reports when a reading has stopped moving.

    Settled means the whole of the last ``window`` seconds fits inside a ``band``
    wide envelope. A range test rather than a slope test: a slow ramp and a noisy
    plateau look alike to a slope, but only one of them stays inside the band.

    Args:
        window: How long the reading must stay put.
        band: How much it may move in that time and still count as still. Set it
            above the sensor noise or nothing ever settles; the SHT45's ±1%RH is
            the floor.
    """

    def __init__(self, window: Positive, band: Positive) -> None:
        self.window = window
        self.band = band
        self._samples: deque[Sample] = deque()

    def reset(self) -> None:
        """Forget everything seen so far, so the next window starts here."""
        self._samples.clear()

    @property
    def mean(self) -> float:
        """The mean over the current window: the plateau value, once settled."""
        return fmean(sample.value for sample in self._samples) if self._samples else 0.0

    def push(self, time: float, value: float) -> bool:
        """Add a reading and say whether the window is now steady.

        Args:
            time: When the reading was taken.
            value: What it read.

        Returns:
            Whether the last ``window`` seconds all sit within ``band``. False
            until the window has filled, so a fresh detector never reports
            settled on its first sample.
        """
        self._samples.append(Sample(time, value))
        # One sample older than the window is kept, so the span the test runs
        # over is genuinely at least ``window`` and not one sample short of it.
        while len(self._samples) > 1 and time - self._samples[1].time >= self.window:
            self._samples.popleft()
        if time - self._samples[0].time < self.window:
            return False
        values = [sample.value for sample in self._samples]
        return max(values) - min(values) <= self.band


def _reaches(fractions: list[Sample], target: float) -> float:
    """The time the normalised response first reaches ``target``, interpolated.

    Args:
        fractions: The response normalised to run 0 → 1, timed from the step.
        target: The fraction to find.

    Returns:
        The interpolated crossing time.

    Raises:
        ResponseTooSmallError: If the response never gets there.
    """
    previous = fractions[0]
    for sample in fractions[1:]:
        if sample.value >= target:
            rise = sample.value - previous.value
            if rise <= 0.0:
                return sample.time
            return previous.time + (target - previous.value) * (sample.time - previous.time) / rise
        previous = sample
    raise ResponseTooSmallError(f"the response never reached {target:.0%} of its final change")


def _shape(elapsed: float, tau: float, dead_time: float) -> float:
    """The normalised FOPDT step response, running 0 → 1."""
    after_delay = elapsed - dead_time
    return 0.0 if after_delay <= 0.0 else 1.0 - exp(-after_delay / tau)


def _sse(fractions: list[Sample], tau: float, dead_time: float) -> float:
    """Squared error of a candidate (τ, θ) against the normalised response."""
    return sum((sample.value - _shape(sample.time, tau, dead_time)) ** 2 for sample in fractions)


def _refine(
    fractions: list[Sample], tau: float, dead_time: float, passes: int = 8, points: int = 7
) -> tuple[float, float]:
    """Least-squares polish of (τ, θ) by a grid that halves each pass.

    The two-point seed reads two samples and trusts them; this reads all of them.
    Deterministic and derivative-free, which matters more here than speed: a few
    hundred evaluations of a scalar exponential is nothing next to the minutes
    the experiment itself took.

    Args:
        fractions: The normalised response, timed from the step.
        tau: Seed time constant.
        dead_time: Seed dead time.
        passes: How many times to halve the search box.
        points: Grid resolution per axis, per pass.

    Returns:
        The best (τ, θ) found.
    """
    best = _sse(fractions, tau, dead_time)
    tau_span = dead_span = tau * 0.5
    floor = tau * 1e-3
    for _ in range(passes):
        centre_tau, centre_dead = tau, dead_time
        for i in range(points):
            candidate_tau = max(centre_tau + tau_span * (2 * i / (points - 1) - 1), floor)
            for j in range(points):
                candidate_dead = max(centre_dead + dead_span * (2 * j / (points - 1) - 1), 0.0)
                sse = _sse(fractions, candidate_tau, candidate_dead)
                if sse < best:
                    best, tau, dead_time = sse, candidate_tau, candidate_dead
        tau_span *= 0.5
        dead_span *= 0.5
    return tau, dead_time


def fit_fopdt(
    samples: list[Sample],
    start: float,
    initial: float,
    final: float,
    size: float,
    refine: bool = True,
) -> FOPDT:
    """Fit a first order plus dead time model to a logged step response.

    Args:
        samples: Readings spanning the step. Anything before ``start`` is ignored.
        start: When the input step was applied.
        initial: The plateau before the step.
        final: The plateau after it.
        size: How far the input moved. Signed, and in the input's own units.
        refine: Whether to least-squares polish the two-point estimate.

    Returns:
        The fitted model, with :attr:`FOPDT.error` giving the RMS residual in
        reading units.

    Raises:
        ResponseTooSmallError: If the reading did not move, or never covered
            enough of its own change to locate the two points.
    """
    change = final - initial
    if not change or not size:
        raise ResponseTooSmallError("the reading did not move between the two plateaus")

    fractions = [
        Sample(sample.time - start, (sample.value - initial) / change)
        for sample in samples
        if sample.time >= start
    ]
    high = _reaches(fractions, _HIGH)
    tau = 1.5 * (high - _reaches(fractions, _LOW))
    dead_time = max(high - tau, 0.0)
    if tau <= 0.0:
        raise ResponseTooSmallError("the response rose too abruptly to time")

    if refine:
        tau, dead_time = _refine(fractions, tau, dead_time)

    residual = (_sse(fractions, tau, dead_time) / len(fractions)) ** 0.5
    return FOPDT(
        gain=change / size,
        tau=tau,
        dead_time=dead_time,
        error=residual * abs(change),
    )
