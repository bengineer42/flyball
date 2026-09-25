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

from flyball.foundation.quantities.curves import Linear
from flyball.foundation.quantities.curves import Table as TableCurve
from flyball.foundation.quantities.errors import CurveNotInvertibleError
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
    channel-unit-per-second, e.g. extra watts per °C/min of ramp. The line
    itself is a [Linear][flyball.foundation.quantities.curves.Linear] curve.
    """

    def __init__(self, gain: float, bias: float = 0.0, rate_gain: float | None = None) -> None:
        self._curve = Linear(scale=gain, offset=bias)
        self.rate_gain = rate_gain

    @property
    def gain(self) -> float:
        return self._curve.scale

    @property
    def bias(self) -> float:
        return self._curve.offset

    def __call__(self, setpoint: float, rate: float = 0.0) -> float:
        demand = self._curve(setpoint)
        if self.rate_gain is not None:
            demand += self.rate_gain * rate
        return demand

    def invert(self, demand: float, rate: float = 0.0) -> float:
        if self.rate_gain is not None:
            demand -= self.rate_gain * rate
        try:
            return self._curve.invert(demand)
        except CurveNotInvertibleError as e:
            raise FeedforwardNotInvertibleError(self.type, "gain is 0") from e


class Table(Feedforward, type="table"):
    """Piecewise-linear `(setpoint, demand)` breakpoints: a static curve measured on the rig.

    Held flat beyond the ends. `rate_gain` adds the same plant-capacity term
    as [Affine][flyball.control.feedforward.Affine]'s, on top of the curve.
    The breakpoints are a [Table][flyball.foundation.quantities.curves.Table]
    curve, which interpolates and inverts them.
    """

    def __init__(self, points: list[tuple[float, float]], rate_gain: float | None = None) -> None:
        self._curve = TableCurve(tuple((x, y) for x, y in points))
        self.rate_gain = rate_gain

    @property
    def points(self) -> list[tuple[float, float]]:
        """The breakpoints, sorted by setpoint."""
        return list(self._curve.points)

    def __call__(self, setpoint: float, rate: float = 0.0) -> float:
        demand = self._curve(setpoint)
        if self.rate_gain is not None:
            demand += self.rate_gain * rate
        return demand

    def invert(self, demand: float, rate: float = 0.0) -> float:
        if self.rate_gain is not None:
            demand -= self.rate_gain * rate
        try:
            return self._curve.invert(demand)
        except CurveNotInvertibleError as e:
            raise FeedforwardNotInvertibleError(self.type, "not monotonic") from e
