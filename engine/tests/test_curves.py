"""Curves: a float in, a float out, and back where monotonic; held flat beyond the domain."""

from __future__ import annotations

import math

import pytest

from flyball.control import Table as FeedforwardTable
from flyball.foundation.errors import UnachievableError
from flyball.foundation.quantities.curves import MAX_POINTS, Linear, Table
from flyball.foundation.quantities.errors import CurveNotInvertibleError


class TestLinear:
    def test_maps_and_inverts(self):
        curve = Linear(scale=2.5, offset=-1.0)
        assert curve(4.0) == 9.0
        assert curve.invert(9.0) == 4.0
        assert curve.monotonic and curve.domain == (-math.inf, math.inf)

    def test_a_zero_scale_does_not_invert(self):
        curve = Linear(scale=0.0, offset=3.0)
        assert curve(123.0) == 3.0 and not curve.monotonic
        with pytest.raises(CurveNotInvertibleError, match="scale is 0") as e:
            curve.invert(3.0)
        assert isinstance(e.value, UnachievableError)

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_refuses_a_non_finite_parameter(self, bad):
        with pytest.raises(ValueError, match="finite"):
            Linear(scale=bad)
        with pytest.raises(ValueError, match="finite"):
            Linear(scale=1.0, offset=bad)

    def test_in_domain_is_any_finite_number(self):
        curve = Linear(scale=1.0)
        assert curve.in_domain(-1e300) and not curve.in_domain(math.nan)


class TestTable:
    def test_interpolates_between_points_given_in_any_order(self):
        curve = Table(((10.0, 100.0), (0.0, 0.0), (5.0, 20.0)))
        assert curve.points == ((0.0, 0.0), (5.0, 20.0), (10.0, 100.0))
        assert curve(2.5) == 10.0
        assert curve(7.5) == 60.0
        assert curve.domain == (0.0, 10.0)

    def test_holds_flat_beyond_the_domain_and_says_so(self):
        curve = Table(((0.0, 1.0), (1.0, 3.0)))
        assert curve(-5.0) == 1.0 and curve(9.0) == 3.0
        assert curve.in_domain(0.0) and curve.in_domain(1.0)
        assert not curve.in_domain(-5.0) and not curve.in_domain(1.0001)
        assert not curve.in_domain(math.nan)

    def test_a_single_point_is_a_constant(self):
        curve = Table(((2.0, 7.0),))
        assert curve(-1.0) == curve(2.0) == curve(10.0) == 7.0

    def test_a_flat_run_inverts_to_its_lowest_x_when_rising(self):
        # A pump with no flow below 0.13 duty: flow 0 maps back to duty 0.
        curve = Table(((0.0, 0.0), (0.13, 0.0), (1.0, 1.9)))
        assert curve.monotonic
        assert curve.invert(0.0) == 0.0
        assert curve.invert(0.05) == pytest.approx(0.13 + 0.87 * 0.05 / 1.9)
        assert curve.invert(1.9) == 1.0

    def test_a_falling_table_inverts(self):
        curve = Table(((0.0, 10.0), (1.0, 5.0), (2.0, 0.0)))
        assert curve.monotonic
        assert curve.invert(7.5) == pytest.approx(0.5)
        assert curve.invert(2.5) == pytest.approx(1.5)

    def test_inverse_holds_flat_beyond_the_range(self):
        curve = Table(((0.0, 0.0), (1.0, 2.0)))
        assert curve.invert(-3.0) == 0.0 and curve.invert(5.0) == 1.0

    def test_a_table_that_turns_back_does_not_invert(self):
        curve = Table(((0.0, 0.0), (1.0, 2.0), (2.0, 1.0)))
        assert not curve.monotonic
        assert curve(1.5) == 1.5  # it still maps forward
        with pytest.raises(CurveNotInvertibleError, match="not monotonic"):
            curve.invert(1.0)

    def test_round_trips_on_a_strictly_monotonic_table(self):
        curve = Table(((0.2, 0.0), (1.1, 100.0), (2.4, 1000.0)))
        for x in (0.2, 0.5, 1.1, 1.7, 2.4):
            assert curve.invert(curve(x)) == pytest.approx(x)

    def test_nan_in_is_nan_out(self):
        curve = Table(((0.0, 0.0), (1.0, 1.0)))
        assert math.isnan(curve(math.nan)) and math.isnan(curve.invert(math.nan))

    def test_refuses_no_points_too_many_or_a_non_finite_one(self):
        with pytest.raises(ValueError, match="at least one"):
            Table(())
        with pytest.raises(ValueError, match=f"at most {MAX_POINTS}"):
            Table(tuple((float(i), 0.0) for i in range(MAX_POINTS + 1)))
        with pytest.raises(ValueError, match="finite"):
            Table(((0.0, 0.0), (math.inf, 1.0)))
        with pytest.raises(ValueError, match="finite"):
            Table(((0.0, math.nan),))
        assert len(Table(tuple((float(i), 0.0) for i in range(MAX_POINTS))).points) == MAX_POINTS

    def test_a_step_uses_the_smaller_y_at_its_x(self):
        curve = Table(((0.0, 0.0), (1.0, 0.0), (1.0, 5.0), (2.0, 5.0)))
        assert curve(1.0) == 0.0 and curve(1.5) == 5.0


@pytest.mark.parametrize(
    "points",
    [
        [  # examples/furnace/rig.yaml, heaters.heater2
            (20.0, 0.0),
            (100.0, 125.4),
            (200.0, 289.4),
            (300.0, 465.6),
            (400.0, 659.8),
            (500.0, 878.7),
            (600.0, 1130.3),
            (700.0, 1423.5),
            (800.0, 1768.3),
            (900.0, 2175.9),
            (1000.0, 2658.5),
            (1100.0, 3229.4),
        ],
        [(0.0, 10.0), (1.0, 5.0), (2.0, 0.0)],
        [(0.0, 0.0), (0.13, 0.0), (1.0, 1.9)],
        [(3.0, 1.0)],
    ],
)
def test_matches_the_feedforward_table_it_will_replace(points):
    """P1 moves feedforward `Table` onto this; the numbers must not move with it."""
    curve, feedforward = Table(tuple(points)), FeedforwardTable(points)
    lo, hi = curve.domain
    xs = [lo - 1.0, *(lo + (hi - lo) * k / 16 for k in range(17)), hi + 1.0]
    for x in xs:
        assert curve(x) == feedforward(x)
    ys = sorted({y for _, y in points})
    for y in [ys[0] - 1.0, *ys, (ys[0] + ys[-1]) / 2, ys[-1] + 1.0]:
        assert curve.invert(y) == feedforward.invert(y)
