"""A loop's demand is feedforward(setpoint) + correction, in the actuator's unit."""

import pytest

from flyball.control import Affine, Feedforwards, Loop, NoFeedforward, Setpoint, Table, Transfer
from flyball.control.laws import P
from flyball.core import Reading
from flyball.core.reading import Measurand, Source
from flyball.core.sink import Actuator
from flyball.core.units.si import Celsius, Watt
from flyball.sim import SteppedClock
from helpers import sample


class Heater(Actuator):
    demand_unit = Watt

    def __init__(self, name: str = "heater") -> None:
        super().__init__(name)
        self.demands: list[float] = []

    def set_demand(self, demand: float) -> float | None:
        self.demands.append(demand)
        return min(demand, 100.0)  # a 100 W heater


class TestFeedforwards:
    def test_builtins_and_their_configs_round_trip(self):
        assert set(Feedforwards) >= {"setpoint", "none", "affine", "table"}
        assert Setpoint()(50.0) == 50.0
        assert NoFeedforward()(50.0) == 0.0
        affine = Affine.config.model_validate({"tag": "affine", "gain": 2.0, "bias": 1.0}).build()
        assert affine(3.0) == 7.0
        assert affine.config.model_dump() == {"tag": "affine", "gain": 2.0, "bias": 1.0}

    def test_table_interpolates_and_holds_flat_past_the_ends(self):
        table = Table([(100, 10.0), (0, 0.0), (200, 40.0)])
        assert table(50) == 5.0
        assert table(150) == 25.0
        assert table(-10) == 0.0
        assert table(500) == 40.0
        assert table.config.points[0] == (0, 0.0)
        with pytest.raises(ValueError):
            Table([])


TEMPERATURE = Measurand("ff_temperature", Celsius)
PROBE = Source("ff_probe", [TEMPERATURE])


def reading(value: float, time_ns: int = 0) -> Reading:
    return Reading(sample(PROBE, TEMPERATURE, value, time_ns), TEMPERATURE, time_ns, value)


class TestLoop:
    def test_demand_is_feedforward_plus_correction_in_the_actuator_unit(self):
        clock = SteppedClock()
        heater = Heater()
        loop = Loop(clock, heater, law=P(kp=2.0), feedforward=Affine(gain=1.0, bias=10.0))
        loop.tick(reading(40.0, clock.now_ns()))
        loop.regulate(50.0, transfer=Transfer.RESET)
        loop.tick(reading(45.0, clock.now_ns()))
        # feedforward(50) = 60 W; the law adds kp * (50 - 45) = 10 W.
        assert loop.demand == pytest.approx(70.0)
        assert loop.correction == pytest.approx(10.0)
        assert loop.settings.demand_unit == "W"
        assert loop.view.feedforward.model_dump() == {"tag": "affine", "gain": 1.0, "bias": 10.0}

    def test_delivered_correction_is_measured_from_the_feedforward(self):
        clock = SteppedClock()
        heater = Heater()
        loop = Loop(clock, heater, law=P(kp=10.0), feedforward=Affine(gain=1.0))
        loop.regulate(90.0, transfer=Transfer.RESET)
        loop.tick(reading(80.0, clock.now_ns()))
        assert loop.demand == pytest.approx(190.0)  # 90 + 10 * 10
        assert loop.expected == pytest.approx(100.0)  # the heater's ceiling
        assert loop.delivered_correction == pytest.approx(10.0)  # 100 - feedforward(90)

    def test_bumpless_seed_subtracts_the_feedforward(self):
        clock = SteppedClock()
        heater = Heater()
        loop = Loop(clock, heater, law=P(kp=1.0), feedforward=Affine(gain=1.0, bias=5.0))
        loop.tick(reading(20.0, clock.now_ns()))
        heater.set_demand(30.0)
        loop.demand = loop.expected = 30.0  # what a manual demand left the heater at
        result = loop.regulate(20.0, transfer=Transfer.TRACK)
        # Held at 30 W; feedforward(20) = 25 W, so the seed must be 5 W to hold the output.
        # P has no integral to carry it, so the bump reports the difference.
        assert result.bump == pytest.approx(-5.0)
        assert loop.feedforward(20.0) == 25.0

    def test_default_is_the_setpoint(self):
        loop = Loop(SteppedClock(), Heater(), law=P(kp=1.0))
        assert isinstance(loop.feedforward, Setpoint)
        assert loop.settings.feedforward.model_dump() == {"tag": "setpoint"}
