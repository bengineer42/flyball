"""A loop on a simulated plant: does PI control actually converge?"""

from __future__ import annotations

import pytest

from flyball.control import PI
from flyball.core.reading import Measurand, Source
from flyball.core.units.si import Celsius
from flyball.runtime.rig import Rig
from flyball.sim import Lag, RecordingActuator, SteppedClock
from helpers import sample


class LagHeater(RecordingActuator):
    """An actuator whose demand drives a first-order plant."""

    def __init__(self, name: str, plant: Lag) -> None:
        super().__init__(name)
        self.plant = plant

    def set_demand(self, demand: float) -> float | None:
        super().set_demand(demand)
        return None


def test_pi_loop_settles_on_a_lag_plant(fresh):
    temperature = Measurand(fresh("temperature"), Celsius)
    chamber = Source(fresh("chamber"), (temperature,))
    plant = Lag(tau_s=5.0, value=20.0)
    heater = LagHeater(fresh("heater"), plant)
    clock = SteppedClock(0)
    rig = Rig()
    rig.clock = clock
    rig.attach_loop(chamber[temperature], heater, law=PI(kp=0.5, ki=0.2))
    loop = rig.loops[heater.name]

    rig.on_read([sample(chamber, temperature, plant.value, clock.now_ns())])
    loop.regulate(50.0)

    dt = 0.5
    history = []
    for i in range(400):
        clock.advance(dt)
        # the plant follows the last demand for one tick, then is read
        value = plant.drive(heater.demands[-1], dt)
        rig.on_read([sample(chamber, temperature, value, clock.now_ns(), seq=i + 2)])
        history.append(value)

    assert history[-1] == pytest.approx(50.0, abs=0.5)
    assert max(history) < 60.0, "no gross overshoot"
    assert loop.state.mode.value == "regulating"


def test_loop_view_joins_settings_and_state(fresh):
    temperature = Measurand(fresh("temperature"), Celsius)
    src = Source(fresh("s"), (temperature,))
    rig = Rig()
    heater = RecordingActuator(fresh("h"))
    rig.attach_loop(src[temperature], heater, law=PI(kp=1.0, ki=0.1))
    loop = rig.loops[heater.name]
    view = loop.view
    assert view.name == heater.name
    assert view.law is not None and view.law.tag == "PI"
    assert loop.settings.law is not None and loop.settings.law.kp == 1.0


def test_min_period_caps_how_often_the_law_steps(fresh):
    temperature = Measurand(fresh("temperature"), Celsius)
    src = Source(fresh("s"), (temperature,))
    clock = SteppedClock(0)
    rig = Rig()
    rig.clock = clock
    heater = RecordingActuator(fresh("h"))
    rig.attach_loop(src[temperature], heater, law=PI(kp=1.0), min_period_s=0.1)
    loop = rig.loops[heater.name]
    assert loop.settings.min_period_s == 0.1
    loop.regulate(10.0)
    demands_before = len(heater.demands)

    # ten readings 10 ms apart: the reading always updates, the law steps once
    for i in range(10):
        clock.advance(0.01)
        rig.on_read([sample(src, temperature, float(i), clock.now_ns(), seq=i + 1)])
    assert loop.reading is not None and loop.reading.value == 9.0
    assert len(heater.demands) == demands_before + 1

    clock.advance(0.1)
    rig.on_read([sample(src, temperature, 5.0, clock.now_ns(), seq=11)])
    assert len(heater.demands) == demands_before + 2, "a reading past the period steps again"


def test_a_generator_builds_from_its_config_and_views_itself_with_or_without_an_end():
    """A ramp lands, so it has an end time; a trajectory that only approaches its point has none."""
    import math

    from flyball.control.setpoint import LinearRampSetpoint, SetPointGenerator

    class Approach(SetPointGenerator, tag="approach", register=False):
        """Closes the gap to `to` with time constant `tau`: never quite arrives."""

        def __init__(self, to: float, tau: float) -> None:
            self.to = to
            self.tau = tau

        def start(self, time: float, value: float) -> None:
            super().start(time, value)
            self.origin, self.span = time, self.to - value

        def generate(self, time: float) -> float:
            return self.to - self.span * math.exp(-(time - self.origin) / self.tau)

        def rate(self, time: float) -> float:
            return self.span / self.tau * math.exp(-(time - self.origin) / self.tau)

        def destination(self) -> float:
            return self.to

    ramp = LinearRampSetpoint.config(tag="ramp", to=80.0, pace={"minutes": 3}).build()
    ramp.start(100.0, 20.0)
    view = ramp.trajectory(100.0, origin_ns=0).model_dump()
    assert view == {
        "tag": "ramp",
        "start": 20.0,
        "start_time_ns": 100 * 10**9,
        "to": 80.0,
        "end_time_ns": 280 * 10**9,
        "rate": 60 / 180,
    }
    assert ramp.config.model_dump()["to"] == 80.0  # the running one still says how it was built

    approach = Approach.config(to=80.0, tau=30.0).build()
    approach.start(100.0, 20.0)
    assert approach.generate(130.0) == pytest.approx(80.0 - 60.0 * math.exp(-1))
    view = approach.trajectory(130.0, origin_ns=0).model_dump()
    assert (
        view["to"] == 80.0
        and view["end_time_ns"] is None
        and view["rate"] == pytest.approx(2 * math.exp(-1))
    )
