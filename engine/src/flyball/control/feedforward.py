"""Feedforwards: the open-loop guess at a demand for a setpoint; the law corrects the rest.

A loop's demand is `feedforward(setpoint, rate) + correction`. The
feedforward maps the channel's unit to the actuator's: the identity when
they agree (a demand of "50 °C" to a controller that takes °C), a static
model of the plant when they do not (the drive that holds 50 °C in this
furnace). `rate` is the setpoint's own rate of change (per second, in the
channel's unit) — zero unless the reference is ramping — and a feedforward
that models a plant with capacity (thermal, hydraulic, ...) can spend an
extra `rate_gain * rate` to charge or discharge it, rather than let the law
find that shortfall through its integral while the ramp is under way.
Subclassing generates `config` from `__init__` and registers the type,
exactly as [ControlLaw][flyball.model.law.ControlLaw] does.

`Feedforward`/`FeedforwardConfig` (the base) and `Identity`/`NoFeedforward`
(the two defaults `Controller` falls back to when none is given) live in
`flyball.model.feedforward` -- `Controller` needs them and `model` can't
import upward from `control`. What's left here are the feedforwards that
model an actual plant.
"""

from __future__ import annotations

from bisect import bisect_left
from itertools import pairwise

from flyball.model.errors import FeedforwardNotInvertibleError

# Re-exported for anyone importing the base/defaults from their old home.
from flyball.model.feedforward import (  # ruff: ignore[unused-import]
    Feedforward,
    FeedforwardConfig,
    FeedforwardLike,
    Identity,
    NoFeedforward,
)


class Affine(Feedforward, type="affine"):
    """`demand = gain * setpoint + bias [+ rate_gain * rate]`.

    The two-number model that fits most plants nearby, plus an optional
    third for one with capacity: `rate_gain` is actuator unit per
    channel-unit-per-second, e.g. extra watts per °C/min of ramp.
    """

    def __init__(self, gain: float, bias: float = 0.0, rate_gain: float | None = None) -> None:
        self.gain = gain
        self.bias = bias
        self.rate_gain = rate_gain

    def __call__(self, setpoint: float, rate: float = 0.0) -> float:
        demand = self.gain * setpoint + self.bias
        if self.rate_gain is not None:
            demand += self.rate_gain * rate
        return demand

    def invert(self, demand: float, rate: float = 0.0) -> float:
        if not self.gain:
            raise FeedforwardNotInvertibleError(self.type, "gain is 0")
        if self.rate_gain is not None:
            demand -= self.rate_gain * rate
        return (demand - self.bias) / self.gain


class Table(Feedforward, type="table"):
    """Piecewise-linear `(setpoint, demand)` breakpoints: a static curve measured on the rig.

    Held flat beyond the ends. `rate_gain` adds the same plant-capacity term
    as [Affine][flyball.control.feedforward.Affine]'s, on top of the curve.
    """

    def __init__(self, points: list[tuple[float, float]], rate_gain: float | None = None) -> None:
        if not points:
            raise ValueError("at least one point")
        self.points = sorted(points)
        self._x = [x for x, _ in self.points]
        self.rate_gain = rate_gain

    def __call__(self, setpoint: float, rate: float = 0.0) -> float:
        demand = self._interpolate(setpoint)
        if self.rate_gain is not None:
            demand += self.rate_gain * rate
        return demand

    def _interpolate(self, setpoint: float) -> float:
        i = bisect_left(self._x, setpoint)
        if i == 0:
            return self.points[0][1]
        if i == len(self.points):
            return self.points[-1][1]
        (x0, y0), (x1, y1) = self.points[i - 1], self.points[i]
        return y0 if x1 == x0 else y0 + (y1 - y0) * (setpoint - x0) / (x1 - x0)

    def invert(self, demand: float, rate: float = 0.0) -> float:
        if self.rate_gain is not None:
            demand -= self.rate_gain * rate
        ys = [y for _, y in self.points]
        ascending = all(a <= b for a, b in pairwise(ys))
        descending = all(a >= b for a, b in pairwise(ys))
        if not (ascending or descending):
            raise FeedforwardNotInvertibleError(self.type, "not monotonic")
        # Re-sort by demand: for a monotonic table this is either `points`
        # unchanged (ascending) or reversed (descending), and the inverse of
        # each linear segment is itself linear.
        by_demand = self.points if ascending else list(reversed(self.points))
        y_sorted = [y for _, y in by_demand]
        i = bisect_left(y_sorted, demand)
        if i == 0:
            return by_demand[0][0]
        if i == len(by_demand):
            return by_demand[-1][0]
        (x0, y0), (x1, y1) = by_demand[i - 1], by_demand[i]
        return x0 if y1 == y0 else x0 + (x1 - x0) * (demand - y0) / (y1 - y0)
