"""A controller: a loop keyed by the signal it drives."""

from __future__ import annotations

import pytest

from flyball.control import Affine, Controller, NoFeedforward, Setpoint, Transfer
from flyball.control.laws import P
from flyball.core.device import Device
from flyball.core.errors import ConflictError
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, Reading, Sample, SignalSpec, WriteState
from flyball.core.units.si import Celsius, Watt
from flyball.sim import SteppedClock

TEMP = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)


class Furnace(Device):
    TREE = (
        SignalSpec(name="zone1", quantity=TEMP, access=Access.RP),
        SignalSpec(name="setpoint", quantity=TEMP, access=Access.RW),
        SignalSpec(name="heater1", quantity=POWER, access=Access.W, limits=(0.0, 2500.0)),
        SignalSpec(name="bath", quantity=TEMP, access=Access.W),
    )


@pytest.fixture
def furnace() -> Furnace:
    return Furnace("furnace")


def test_named_by_its_target(furnace):
    controller = Controller(SteppedClock(), furnace.signals["heater1"], furnace.signals["zone1"])
    assert controller.name == "furnace.heater1"
    assert controller.target is furnace.signals["heater1"]
    assert controller.source is furnace.signals["zone1"]
    assert controller.demand_unit is Watt
    assert controller.settings.name == "furnace.heater1"
    assert controller.settings.demand_unit == "W"
    assert not hasattr(controller, "actuator")


def test_the_target_must_be_writable_and_the_source_publishing(furnace):
    clock = SteppedClock()
    with pytest.raises(ConflictError, match=r"furnace.zone1 \[rp\] is not writable"):
        Controller(clock, furnace.signals["zone1"], furnace.signals["zone1"])
    with pytest.raises(ConflictError, match=r"furnace.setpoint \[rw\] is not publishing"):
        Controller(clock, furnace.signals["heater1"], furnace.signals["setpoint"])


def test_feedforward_defaults_follow_the_units(furnace):
    clock = SteppedClock()
    same = Controller(clock, furnace.signals["bath"], furnace.signals["zone1"])
    assert isinstance(same.feedforward, Setpoint)
    different = Controller(clock, furnace.signals["heater1"], furnace.signals["zone1"])
    assert isinstance(different.feedforward, NoFeedforward)
    with pytest.raises(
        ConflictError,
        match=r"controller on furnace.zone1 \(°C\) cannot pass its setpoint to 'furnace.heater1',"
        r" which takes demands in W",
    ):
        Controller(
            clock, furnace.signals["heater1"], furnace.signals["zone1"], feedforward=Setpoint()
        )
    built = Controller(
        clock,
        furnace.signals["heater1"],
        furnace.signals["zone1"],
        feedforward=Affine.config(gain=10.0, bias=0.0),
    )
    assert isinstance(built.feedforward, Affine)


def test_on_reading_ticks_and_writes(furnace):
    clock = SteppedClock(0)
    writes: list[float] = []

    def write(demand: float) -> float | None:
        writes.append(demand)
        return min(demand, 2500.0)

    controller = Controller(
        clock,
        furnace.signals["heater1"],
        furnace.signals["zone1"],
        law=P(kp=100.0),
        feedforward=Affine(gain=10.0, bias=0.0),
        write=write,
    )
    zone1 = furnace.signals["zone1"]
    controller.on_reading(Reading(zone1, clock.now_ns(), 20.0))
    assert controller.reading is not None and controller.reading.value == 20.0
    assert writes == [], "manual: nothing written"
    controller.regulate(50.0, transfer=Transfer.RESET)
    assert controller.setpoint == 50.0 and writes == [500.0]

    clock.advance(1.0)
    sample = Sample(furnace.root, clock.now_ns(), {"zone1": 30.0})
    controller.on_reading(next(sample.readings()))
    assert writes == [500.0, 500.0 + 100.0 * 20.0]
    assert controller.demand == 2500.0 and controller.expected == 2500.0
    assert controller.delivered_correction == 2000.0

    with pytest.raises(AssertionError, match="is not"):
        controller.on_reading(Reading(furnace.signals["setpoint"], clock.now_ns(), 1.0))


def test_unwired_records_the_demand_and_writes_nothing(furnace):
    clock = SteppedClock(0)
    controller = Controller(clock, furnace.signals["bath"], furnace.signals["zone1"], law=P(kp=2.0))
    controller.on_reading(Reading(furnace.signals["zone1"], 0, 40.0))
    controller.regulate(50.0, transfer=Transfer.RESET)
    assert controller.demand == 50.0, "the setpoint itself: same unit, correction reset"
    controller.on_reading(Reading(furnace.signals["zone1"], 1_000_000_000, 40.0))
    assert controller.demand == 50.0 + 2.0 * 10.0
    assert controller.expected is None and controller.delivered_correction is None


def test_delivered_closes_a_deferred_write(furnace):
    clock = SteppedClock(0)
    controller = Controller(
        clock,
        furnace.signals["heater1"],
        furnace.signals["zone1"],
        law=P(kp=100.0),
        feedforward=Affine(gain=10.0, bias=0.0),
        write=lambda demand: None,
    )
    controller.on_reading(Reading(furnace.signals["zone1"], 0, 30.0))
    controller.regulate(50.0, transfer=Transfer.RESET)
    controller.on_reading(Reading(furnace.signals["zone1"], 1_000_000_000, 30.0))
    assert controller.demand == 500.0 + 100.0 * 20.0
    assert controller.expected is None and controller.delivered_correction is None

    controller.delivered(WriteState(value=2500.0, requested=2500.0, at_limit="high"))
    assert controller.expected == 2500.0
    assert controller.delivered_correction == 2000.0, (
        "what the hardware took beyond the feedforward"
    )
    assert controller.state.expected == 2500.0

    controller.delivered(WriteState(value=None))
    assert controller.expected is None and controller.delivered_correction is None
