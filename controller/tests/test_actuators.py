"""`output_range`: what a demand can achieve, in the actuator's own unit."""

from __future__ import annotations

import pytest

from flyball.core.sink import Actuator, ActuatorConfig
from flyball.runtime.config import RigConfig
from flyball.sim.devices import SimActuator
from flyball.sim.plant import Lag, Noisy
from helpers import DutyHeater


class TestBaseActuator:
    def test_defaults_to_none(self):
        assert DutyHeater("h").output_range is None

    def test_the_default_state_carries_whatever_is_set(self):
        # DutyHeater overrides `.state` itself and does not surface it (like a
        # real hardware actuator that has not been touched for this yet); the
        # base `Actuator.state`, for anything that does not override it, does.
        heater = DutyHeater("h")
        heater.output_range = (0.0, 10.0)
        assert Actuator.state.fget(heater).output_range == (0.0, 10.0)


class TestSimActuatorOutputRange:
    def test_plain_mode_scales_limits_by_the_watt_scale(self):
        plant = Noisy(Lag(10.0), 0.0)
        heater = SimActuator("heater", plant, limits=(0.0, 1.0), unit="W", power_w=6000.0)
        assert heater.state.output_range == (0.0, 6000.0)
        heater.set_limits(0.2, 0.9)
        assert heater.state.output_range == pytest.approx((1200.0, 5400.0))

    def test_of_full_mode_scales_by_one(self):
        plant = Noisy(Lag(10.0), 0.0)
        drive = SimActuator("drive", plant, limits=(0.1, 0.8), unit="of full")
        assert drive.state.output_range == pytest.approx((0.1, 0.8))

    def test_smart_mode_has_no_fixed_range(self):
        plant = Noisy(Lag(10.0, ambient=20.0), 0.0)
        heater = SimActuator("heater", plant, limits=(0.0, 1.0))  # unit omitted: smart mode
        assert heater.scale is None
        assert heater.state.output_range is None


def test_a_config_can_state_output_range_for_an_actuator_that_cannot_work_it_out(fresh):
    """Real hardware has no generic `limits x scale`; its config may say the range instead."""
    tag = fresh("fake_actuator")

    class FakeActuator(Actuator):
        def set_demand(self, demand: float) -> float | None:
            return None

    class FakeActuatorConfig(ActuatorConfig[FakeActuator], tag=tag):
        name: str

        def build(self) -> FakeActuator:
            return FakeActuator(self.name)

    name = fresh("fake")
    config = RigConfig.model_validate({
        "actuators": [{"tag": tag, "name": name, "output_range": [-10.0, 10.0]}]
    })
    rig = config.build(start=False)
    assert rig.actuators[name].output_range == (-10.0, 10.0)


def test_a_config_that_says_nothing_leaves_it_none(fresh):
    tag = fresh("fake_actuator")

    class FakeActuator(Actuator):
        def set_demand(self, demand: float) -> float | None:
            return None

    class FakeActuatorConfig(ActuatorConfig[FakeActuator], tag=tag):
        name: str

        def build(self) -> FakeActuator:
            return FakeActuator(self.name)

    name = fresh("fake")
    config = RigConfig.model_validate({"actuators": [{"tag": tag, "name": name}]})
    rig = config.build(start=False)
    assert rig.actuators[name].output_range is None
