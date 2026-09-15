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
        value = plant.step(heater.demands[-1], dt)
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
