"""Curves: a pure map from one float to another, and back where it is monotonic.

A calibration (raw volts to a physical value) and a feedforward (setpoint to
demand) are the same object: a function of one float, known on a domain, and
invertible when it never turns back. [Linear][flyball.foundation.quantities.curves.Linear]
and [Table][flyball.foundation.quantities.curves.Table] are the two kinds so far.

Beyond its domain a curve holds flat at the nearest end rather than
extrapolate; [in_domain][flyball.foundation.quantities.curves.Table.in_domain] lets
a caller that must not hold flat (a derived signal, which then has no value)
check first. Both kinds refuse a non-finite parameter when built.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass, field
from itertools import pairwise
from typing import ClassVar

from .errors import CurveNotInvertibleError

MAX_POINTS = 1024
"""The most points a `Table` takes: a calibration, not a data set."""


def _finite(name: str, value: float) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return float(value)


@dataclass(frozen=True, slots=True)
class Linear:
    """`scale * x + offset`, everywhere."""

    type: ClassVar[str] = "linear"
    scale: float
    offset: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "scale", _finite("scale", self.scale))
        object.__setattr__(self, "offset", _finite("offset", self.offset))

    @property
    def domain(self) -> tuple[float, float]:
        return (-math.inf, math.inf)

    @property
    def monotonic(self) -> bool:
        return self.scale != 0

    def in_domain(self, x: float) -> bool:
        return math.isfinite(x)

    def __call__(self, x: float) -> float:
        return self.scale * x + self.offset

    def invert(self, y: float) -> float:
        """The `x` that maps to `y`.

        Raises:
            CurveNotInvertibleError: The scale is 0.
        """
        if self.scale == 0:
            raise CurveNotInvertibleError(self.type, "scale is 0")
        return (y - self.offset) / self.scale


@dataclass(frozen=True, slots=True)
class Table:
    """Piecewise-linear `(x, y)` breakpoints, sorted by `x`; held flat beyond the ends.

    Two points may share an `x` (a step): at that `x` the smaller `y` is used.
    """

    type: ClassVar[str] = "table"
    points: tuple[tuple[float, float], ...]
    _x: tuple[float, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.points:
            raise ValueError("a table needs at least one point")
        if len(self.points) > MAX_POINTS:
            raise ValueError(f"a table takes at most {MAX_POINTS} points, got {len(self.points)}")
        points = tuple(sorted((_finite("x", x), _finite("y", y)) for x, y in self.points))
        object.__setattr__(self, "points", points)
        object.__setattr__(self, "_x", tuple(x for x, _ in points))

    @property
    def domain(self) -> tuple[float, float]:
        return (self._x[0], self._x[-1])

    @property
    def monotonic(self) -> bool:
        """`y` never turns back: all non-decreasing or all non-increasing. Flat runs allowed."""
        ys = [y for _, y in self.points]
        return all(a <= b for a, b in pairwise(ys)) or all(a >= b for a, b in pairwise(ys))

    def in_domain(self, x: float) -> bool:
        lo, hi = self.domain
        return lo <= x <= hi

    def __call__(self, x: float) -> float:
        if math.isnan(x):
            return math.nan
        i = bisect_left(self._x, x)
        if i == 0:
            return self.points[0][1]
        if i == len(self.points):
            return self.points[-1][1]
        (x0, y0), (x1, y1) = self.points[i - 1], self.points[i]
        return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)

    def invert(self, y: float) -> float:
        """The `x` that maps to `y`, held flat beyond the range.

        On a flat run, one end of it: the lowest `x` when `y` rises, the highest when it falls.

        Raises:
            CurveNotInvertibleError: `y` turns back somewhere.
        """
        if not self.monotonic:
            raise CurveNotInvertibleError(self.type, "not monotonic")
        if math.isnan(y):
            return math.nan
        ascending = all(a <= b for a, b in pairwise(p[1] for p in self.points))
        # Monotonic, so sorting by `y` is the points as they are (ascending) or
        # reversed (descending), and each segment's inverse is itself linear.
        by_y = self.points if ascending else tuple(reversed(self.points))
        ys = [p[1] for p in by_y]
        i = bisect_left(ys, y)
        if i == 0:
            return by_y[0][0]
        if i == len(by_y):
            return by_y[-1][0]
        (x0, y0), (x1, y1) = by_y[i - 1], by_y[i]
        return x0 if y1 == y0 else x0 + (x1 - x0) * (y - y0) / (y1 - y0)


type Curve = Linear | Table
"""Every curve kind: a float in, a float out, `invert()` where monotonic."""
