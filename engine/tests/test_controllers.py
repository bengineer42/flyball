"""The controller registry: by target address, one per target and one per source."""

from __future__ import annotations

import pytest
from flyball_sim.clock import SteppedClock

from flyball.control.laws import P
from flyball.model.controller import Controller
from flyball.rig import (
    ControllerNotFoundError,
    Controllers,
    NoDefaultControllerError,
    SignalClaimedError,
)
from test_rig_devices import Furnace


@pytest.fixture
def furnace() -> Furnace:
    return Furnace("furnace")


def _controller(furnace: Furnace, target: str, source: str) -> Controller:
    return Controller(SteppedClock(), furnace.signals[target], furnace.signals[source], law=P(kp=1))


def test_keyed_by_target_address_with_the_first_as_default(furnace):
    controllers = Controllers()
    assert controllers.default is None and len(controllers) == 0
    c1 = _controller(furnace, "heater1", "zone1")
    c2 = _controller(furnace, "heater2", "zone2")
    controllers.add(c1)
    controllers.add(c2)
    assert list(controllers) == ["furnace.heater1", "furnace.heater2"]
    assert controllers["furnace.heater2"] is c2 and "furnace.heater2" in controllers
    assert controllers.default == "furnace.heater1"
    assert controllers.resolve() is c1 and controllers.resolve("furnace.heater2") is c2
    assert controllers.find(furnace.signals["zone2"]) is c2
    assert controllers.find(furnace.signals["sample"]) is None
    assert controllers.driving(furnace.signals["heater1"]) is c1
    assert controllers.driving(furnace.signals["setpoint"]) is None
    assert controllers.measured("furnace.heater1") is furnace.signals["zone1"]
    assert list(controllers.entries()) == [
        (furnace.signals["zone1"], c1),
        (furnace.signals["zone2"], c2),
    ]
    assert dict(controllers.items()) == {"furnace.heater1": c1, "furnace.heater2": c2}
    assert set(controllers.states) == set(controllers.specs) == set(controllers.views)
    assert controllers.views["furnace.heater1"].name == "furnace.heater1"


def test_default_can_be_chosen_and_moves_on_remove(furnace):
    controllers = Controllers()
    c1 = _controller(furnace, "heater1", "zone1")
    c2 = _controller(furnace, "heater2", "zone2")
    controllers.add(c1)
    controllers.add(c2, default=True)
    assert controllers.default == "furnace.heater2"
    assert controllers.remove("furnace.heater2") is c2
    assert controllers.default == "furnace.heater1"
    assert controllers.find(furnace.signals["zone2"]) is None
    assert controllers.driving(furnace.signals["heater2"]) is None
    controllers.remove("furnace.heater1")
    assert controllers.default is None
    with pytest.raises(NoDefaultControllerError, match="No default controller set"):
        controllers.resolve()
    with pytest.raises(ControllerNotFoundError, match="Controller 'furnace.heater1' not found"):
        controllers.resolve("furnace.heater1")
    with pytest.raises(ControllerNotFoundError, match="Controller 'furnace.heater1' not found"):
        controllers.remove("furnace.heater1")


def test_one_controller_per_target_and_one_per_source(furnace):
    controllers = Controllers()
    c1 = _controller(furnace, "heater1", "zone1")
    controllers.add(c1)
    controllers.add(c1)  # the same one again is a no-op
    assert len(controllers) == 1
    with pytest.raises(
        SignalClaimedError,
        match="furnace.heater1 is already driven by controller 'furnace.heater1'",
    ):
        controllers.add(_controller(furnace, "heater1", "zone2"))
    with pytest.raises(
        SignalClaimedError,
        match="furnace.zone1 is already regulated by controller 'furnace.heater1'",
    ):
        controllers.add(_controller(furnace, "heater2", "zone1"))
    assert list(controllers) == ["furnace.heater1"], "a refused add registers nothing"
    controllers.remove("furnace.heater1")
    controllers.add(_controller(furnace, "heater2", "zone1"))
    assert controllers.find(furnace.signals["zone1"]).name == "furnace.heater2"
