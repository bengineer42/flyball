"""A controller's demand is feedforward(setpoint) + correction, in the target's unit."""

import pytest
from flyball_sim.clock import SteppedClock

from flyball.control import (
    Affine,
    Controller,
    Feedforwards,
    NoFeedforward,
    Setpoint,
    Table,
    Transfer,
)
from flyball.control.errors import FeedforwardNotInvertibleError
from flyball.control.laws import P
from flyball.control.types import ValueSource
from flyball.foundation.device import Access, Device, Reading, SignalSpec
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Watt


class Oven(Device):
    """A °C zone regulated through a 100 W heater."""

    TREE = (
        SignalSpec(name="zone", quantity=Quantity("temperature", Celsius), access=Access.RP),
        SignalSpec(name="heater", quantity=Quantity("power", Watt), access=Access.W),
    )


class Heater:
    """Collects demands and reports what a 100 W heater delivers."""

    def __init__(self) -> None:
        self.demands: list[float] = []

    def write(self, demand: float) -> float | None:
        self.demands.append(demand)
        return min(demand, 100.0)


def controller(clock: SteppedClock, heater: Heater, **kwargs) -> Controller:
    oven = Oven("oven")
    return Controller(
        clock, oven.signals["heater"], oven.signals["zone"], write=heater.write, **kwargs
    )


class TestFeedforwards:
    def test_builtins_and_their_configs_round_trip(self):
        assert set(Feedforwards) >= {"setpoint", "none", "affine", "table"}
        assert Setpoint()(50.0) == 50.0
        assert NoFeedforward()(50.0) == 0.0
        affine = Affine.config.model_validate({"tag": "affine", "gain": 2.0, "bias": 1.0}).build()
        assert affine(3.0) == 7.0
        assert affine.config.model_dump() == {
            "tag": "affine",
            "gain": 2.0,
            "bias": 1.0,
            "rate_gain": None,
        }

    def test_table_interpolates_and_holds_flat_past_the_ends(self):
        table = Table([(100, 10.0), (0, 0.0), (200, 40.0)])
        assert table(50) == 5.0
        assert table(150) == 25.0
        assert table(-10) == 0.0
        assert table(500) == 40.0
        assert table.config.points[0] == (0, 0.0)
        with pytest.raises(ValueError):
            Table([])


class TestInvert:
    """The setpoint behind a demand: identity, algebra, or a table read backwards."""

    def test_setpoint_is_its_own_inverse(self):
        assert Setpoint().invert(50.0) == 50.0

    def test_none_has_no_inverse(self):
        with pytest.raises(FeedforwardNotInvertibleError, match="'none'"):
            NoFeedforward().invert(0.0)

    def test_affine_inverts_the_line(self):
        affine = Affine(gain=2.0, bias=10.0)
        assert affine.invert(affine(30.0)) == pytest.approx(30.0)
        with pytest.raises(FeedforwardNotInvertibleError, match="'affine'"):
            Affine(gain=0.0, bias=5.0).invert(5.0)

    def test_table_inverts_when_monotonic_rising_or_falling(self):
        rising = Table([(0, 0.0), (100, 10.0), (200, 40.0)])
        assert rising.invert(5.0) == pytest.approx(50.0)
        assert rising.invert(25.0) == pytest.approx(150.0)
        assert rising.invert(-10.0) == 0.0, "held flat below the first point"
        assert rising.invert(100.0) == 200, "held flat past the last point"

        falling = Table([(0, 40.0), (100, 10.0), (200, 0.0)])
        assert falling.invert(25.0) == pytest.approx(50.0)

    def test_table_with_a_non_monotonic_curve_has_no_inverse(self):
        bumpy = Table([(0, 0.0), (100, 40.0), (200, 10.0)])
        with pytest.raises(FeedforwardNotInvertibleError, match="not monotonic"):
            bumpy.invert(20.0)


def reading(value: float, time_ns: int = 0) -> Reading:
    return Reading(Oven("probe").signals["zone"], time_ns, value)


class TestController:
    def test_demand_is_feedforward_plus_correction_in_the_target_unit(self):
        clock = SteppedClock()
        heater = Heater()
        loop = controller(clock, heater, law=P(kp=2.0), feedforward=Affine(gain=1.0, bias=10.0))
        loop.tick(reading(40.0, clock.now_ns()))
        loop.regulate(50.0, transfer=Transfer.RESET)
        loop.tick(reading(45.0, clock.now_ns()))
        # feedforward(50) = 60 W; the law adds kp * (50 - 45) = 10 W.
        assert loop.demand == pytest.approx(70.0)
        assert loop.correction == pytest.approx(10.0)
        assert loop.settings.demand_unit == "W"
        assert loop.view.feedforward.model_dump() == {
            "tag": "affine",
            "gain": 1.0,
            "bias": 10.0,
            "rate_gain": None,
        }

    def test_delivered_correction_is_measured_from_the_feedforward(self):
        clock = SteppedClock()
        heater = Heater()
        loop = controller(clock, heater, law=P(kp=10.0), feedforward=Affine(gain=1.0))
        loop.regulate(90.0, transfer=Transfer.RESET)
        loop.tick(reading(80.0, clock.now_ns()))
        assert loop.demand == pytest.approx(190.0)  # 90 + 10 * 10
        assert loop.expected == pytest.approx(100.0)  # the heater's ceiling
        assert loop.delivered_correction == pytest.approx(10.0)  # 100 - feedforward(90)

    def test_bumpless_seed_subtracts_the_feedforward(self):
        clock = SteppedClock()
        heater = Heater()
        loop = controller(clock, heater, law=P(kp=1.0), feedforward=Affine(gain=1.0, bias=5.0))
        loop.tick(reading(20.0, clock.now_ns()))
        loop.demand = loop.expected = 30.0  # what a manual demand left the heater at
        result = loop.regulate(20.0, transfer=Transfer.TRACK)
        # Held at 30 W; feedforward(20) = 25 W, so the seed must be 5 W to hold the output.
        # P has no integral to carry it, so the bump reports the difference.
        assert result.bump == pytest.approx(-5.0)
        assert loop.feedforward(20.0) == 25.0

    def test_default_follows_the_units(self):
        loop = controller(SteppedClock(), Heater(), law=P(kp=1.0))
        assert isinstance(loop.feedforward, NoFeedforward), "°C to W: nothing to pass through"
        assert loop.settings.feedforward.model_dump() == {"tag": "none"}
        bath = Oven("bath")
        bath.signals["heater"].override(quantity=Quantity("temperature", Celsius))
        same = Controller(SteppedClock(), bath.signals["heater"], bath.signals["zone"])
        assert isinstance(same.feedforward, Setpoint)
        assert same.settings.feedforward.model_dump() == {"tag": "setpoint"}

    def test_regulate_at_demand_aims_at_the_setpoint_behind_it(self):
        """`at=DEMAND` must land back in the source's unit, not the target's."""
        clock = SteppedClock()
        heater = Heater()
        loop = controller(clock, heater, law=P(kp=2.0), feedforward=Affine(gain=2.0, bias=10.0))
        loop.regulate(50.0, transfer=Transfer.RESET)
        loop.tick(reading(45.0, clock.now_ns()))
        # demand = feedforward(50) + kp*(50-45) = 110 + 10 = 120 W; the setpoint
        # behind that demand is invert(120) = 55, in the source's unit (°C).
        assert loop.demand == pytest.approx(120.0)
        assert loop.resolve_value(ValueSource.DEMAND) == pytest.approx(55.0)

        result = loop.regulate(ValueSource.DEMAND, transfer=Transfer.RESET)
        assert loop.reference == pytest.approx(55.0)
        assert loop.setpoint == pytest.approx(55.0)
        assert result.demand == pytest.approx(120.0), "correction reset: demand == feedforward(55)"

    def test_regulate_at_demand_without_an_invertible_feedforward_is_a_clear_error(self):
        clock = SteppedClock()
        heater = Heater()
        loop = controller(clock, heater, law=P(kp=2.0), feedforward=NoFeedforward())
        loop.regulate(50.0, transfer=Transfer.RESET)
        loop.tick(reading(45.0, clock.now_ns()))
        with pytest.raises(FeedforwardNotInvertibleError, match="'none'"):
            loop.regulate(ValueSource.DEMAND)
