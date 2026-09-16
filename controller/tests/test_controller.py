"""A controller: one P signal regulated through one W signal, named by the latter."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from flyball.control import (
    PI,
    Affine,
    Controller,
    ControllerMode,
    LinearRampSetpoint,
    NoFeedforward,
    Setpoint,
    SetPointGenerators,
    Transfer,
)
from flyball.control.laws import P
from flyball.core.clock import Speed, TimeUnit
from flyball.core.device import Device
from flyball.core.errors import ConflictError
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, Reading, Sample, SignalSpec, WriteState
from flyball.core.units.si import Celsius, Watt
from flyball.sim.clock import SteppedClock
from flyball.sim.plant import Lag

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
    assert controller.demand_unit == "W"
    settings = controller.settings
    assert settings.name == "furnace.heater1" and settings.demand_unit == "W"
    assert settings.target == "furnace.heater1" and settings.source == "furnace.zone1"
    assert controller.view.target == "furnace.heater1" and controller.view.source == "furnace.zone1"


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
    sample = Sample(furnace.root, clock.now_ns(), {zone1: 30.0})
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


def _regulating(furnace: Furnace, clock: SteppedClock, **kwargs) -> tuple[Controller, list[float]]:
    """A controller on `bath` (the source's unit) whose writes are collected."""
    writes: list[float] = []

    def write(demand: float) -> float | None:
        writes.append(demand)
        return demand

    controller = Controller(
        clock, furnace.signals["bath"], furnace.signals["zone1"], write=write, **kwargs
    )
    return controller, writes


def test_pi_settles_on_a_lag_plant(furnace):
    clock = SteppedClock(0)
    plant = Lag(tau_s=5.0, value=20.0)
    controller, writes = _regulating(furnace, clock, law=PI(kp=0.5, ki=0.2))
    zone1 = furnace.signals["zone1"]
    controller.on_reading(Reading(zone1, clock.now_ns(), plant.value))
    controller.regulate(50.0)

    dt = 0.5
    history = []
    for _ in range(400):
        clock.advance(dt)
        value = plant.drive(writes[-1], dt)  # the plant follows the last demand for one tick
        controller.on_reading(Reading(zone1, clock.now_ns(), value))
        history.append(value)

    assert history[-1] == pytest.approx(50.0, abs=0.5)
    assert max(history) < 60.0, "no gross overshoot"
    assert controller.state.mode is ControllerMode.REGULATING
    assert controller.view.mode.value == "regulating"


def test_view_joins_settings_and_state(furnace):
    controller, _ = _regulating(furnace, SteppedClock(0), law=PI(kp=1.0, ki=0.1))
    view = controller.view
    assert view.name == "furnace.bath"
    assert view.law is not None and view.law.tag == "PI"
    assert controller.settings.law is not None and controller.settings.law.kp == 1.0
    assert view.feedforward.model_dump() == {"tag": "setpoint"}


def test_min_period_caps_how_often_the_law_steps(furnace):
    clock = SteppedClock(0)
    controller, writes = _regulating(furnace, clock, law=PI(kp=1.0), min_period_s=0.1)
    zone1 = furnace.signals["zone1"]
    assert controller.settings.min_period_s == 0.1
    controller.regulate(10.0)
    before = len(writes)

    # ten readings 10 ms apart: the reading always updates, the law steps once
    for i in range(10):
        clock.advance(0.01)
        controller.on_reading(Reading(zone1, clock.now_ns(), float(i)))
    assert controller.reading is not None and controller.reading.value == 9.0
    assert len(writes) == before + 1

    clock.advance(0.1)
    controller.on_reading(Reading(zone1, clock.now_ns(), 5.0))
    assert len(writes) == before + 2, "a reading past the period steps again"


def test_manual_holds_the_demand_and_regulate_resumes_bumplessly(furnace):
    clock = SteppedClock(0)
    controller, writes = _regulating(furnace, clock, law=PI(kp=1.0, ki=0.5))
    zone1 = furnace.signals["zone1"]
    controller.on_reading(Reading(zone1, 0, 40.0))
    controller.regulate(50.0, transfer=Transfer.RESET)
    for i in range(1, 6):
        controller.on_reading(Reading(zone1, i * 1_000_000_000, 40.0))
    held = writes[-1]
    assert held > 50.0, "the integral wound up against a stuck reading"

    controller.manual()
    controller.on_reading(Reading(zone1, 6_000_000_000, 45.0))
    assert writes[-1] == held and controller.mode is ControllerMode.MANUAL, "no tick writes"

    result = controller.regulate(50.0, transfer=Transfer.TRACK)
    assert result.bump == pytest.approx(0.0), "TRACK seeds the law to hold the output"
    assert writes[-1] == pytest.approx(held)
    reset = controller.regulate(50.0, transfer=Transfer.RESET)
    assert reset.demand == 50.0 and reset.bump == pytest.approx(50.0 - held)


def test_linear_ramp_setpoint_config_round_trips_and_builds():
    assert SetPointGenerators["linear_ramp_setpoint"] is LinearRampSetpoint
    config = LinearRampSetpoint.config.model_validate({
        "tag": "linear_ramp_setpoint",
        "pace": {"per_minute": 10},
        "end": 30.0,
    })
    ramp = config.build()
    assert ramp.pace == Speed(10.0, TimeUnit.MINUTE) and ramp.end == 30.0

    with pytest.raises(Exception, match="tag"):
        LinearRampSetpoint.config.model_validate({"tag": "no_such_tag", "pace": 1, "end": 1})


def test_linear_ramp_setpoint_serialises_its_init_args_plus_end_time_once_started():
    ramp = LinearRampSetpoint(Speed(10.0, TimeUnit.MINUTE), 30.0)
    before = TypeAdapter(LinearRampSetpoint).dump_python(ramp, mode="json")
    assert before == {
        "tag": "linear_ramp_setpoint",
        "pace": {"value": 10.0, "per": "minute"},
        "end": 30.0,
    }, "not yet started: no end_time on the wire"

    ramp.start(0.0, 0.0)
    after = TypeAdapter(LinearRampSetpoint).dump_python(ramp, mode="json")
    assert after == {**before, "end_time": pytest.approx(180.0)}
