"""A controller: one P signal regulated through one W signal, named by the latter."""

from __future__ import annotations

import pytest
from flyball_sim.clock import SteppedClock
from flyball_sim.plant import Lag
from pydantic import TypeAdapter

from flyball.control import (
    PI,
    Affine,
    GeneratorConfig,
    Hold,
    LinearRampSetpoint,
    Profile,
    SetPointGenerator,
)
from flyball.control.laws import P
from flyball.foundation.device import (
    Access,
    Device,
    Reading,
    Role,
    Sample,
    SignalSpec,
    WriteState,
)
from flyball.foundation.errors import ConflictError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Watt
from flyball.foundation.time import Duration, Speed, TimeUnit
from flyball.model.catalog import get_catalog
from flyball.model.controller import Controller, ControllerMode
from flyball.model.feedforward import NoFeedforward, Setpoint
from flyball.model.law import Transfer

TEMP = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)


class Furnace(Device):
    TREE = (
        SignalSpec(name="zone1", quantity=TEMP, access=Access.RP),
        SignalSpec(name="setpoint", quantity=TEMP, access=Access.RW, role=Role.DEMAND),
        SignalSpec(
            name="heater1", quantity=POWER, access=Access.W, role=Role.DEMAND, limits=(0.0, 2500.0)
        ),
        SignalSpec(name="bath", quantity=TEMP, access=Access.W, role=Role.DEMAND),
        SignalSpec(name="range", quantity=TEMP, access=Access.RW, role=Role.SETTING),
        SignalSpec(name="flow", quantity=POWER, access=Access.RP, role=Role.DEMAND),
    )


@pytest.fixture
def furnace() -> Furnace:
    return Furnace("furnace")


def test_named_by_its_target(furnace):
    controller = Controller(SteppedClock(), furnace.signals["heater1"], furnace.signals["zone1"])
    assert controller.name == "furnace.heater1"
    assert controller.output_signal is furnace.signals["heater1"]
    assert controller.measured_signal is furnace.signals["zone1"]
    assert controller.output_unit == "W"
    settings = controller.settings
    assert settings.name == "furnace.heater1" and settings.output_unit == "W"
    assert (
        settings.output_signal == "furnace.heater1" and settings.measured_signal == "furnace.zone1"
    )
    assert (
        controller.view.output_signal == "furnace.heater1"
        and controller.view.measured_signal == "furnace.zone1"
    )


def test_the_output_must_be_a_writable_demand_and_the_measured_signal_publishing(furnace):
    """C13: a controller drives only a demand, and only one it may write."""
    clock = SteppedClock()
    zone1 = furnace.signals["zone1"]
    with pytest.raises(ConflictError, match=r"'furnace.zone1' is a readout, not a demand"):
        Controller(clock, zone1, zone1)
    with pytest.raises(ConflictError, match=r"'furnace.range' is a setting, not a demand"):
        Controller(clock, furnace.signals["range"], zone1)  # writable, but a setting
    with pytest.raises(ConflictError, match=r"furnace.flow \[rp\] is not writable"):
        Controller(clock, furnace.signals["flow"], zone1)  # a demand only its group drives
    with pytest.raises(ConflictError, match=r"furnace.setpoint \[rw\] is not published"):
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
    assert controller.measured is not None and controller.measured.value == 20.0
    assert writes == [], "manual: nothing written"
    controller.regulate(50.0, transfer=Transfer.COLD)
    assert controller.setpoint == 50.0 and writes == [500.0]

    clock.advance(1.0)
    sample = Sample(furnace.root, clock.now_ns(), {zone1: 30.0})
    controller.on_reading(next(sample.readings()))
    assert writes == [500.0, 500.0 + 100.0 * 20.0]
    assert controller.output == 2500.0 and controller.expected == 2500.0
    assert controller.delivered_correction == 2000.0

    with pytest.raises(AssertionError, match="is not"):
        controller.on_reading(Reading(furnace.signals["setpoint"], clock.now_ns(), 1.0))


def test_unwired_records_the_demand_and_writes_nothing(furnace):
    clock = SteppedClock(0)
    controller = Controller(clock, furnace.signals["bath"], furnace.signals["zone1"], law=P(kp=2.0))
    controller.on_reading(Reading(furnace.signals["zone1"], 0, 40.0))
    controller.regulate(50.0, transfer=Transfer.COLD)
    assert controller.output == 50.0, "the setpoint itself: same unit, correction reset"
    controller.on_reading(Reading(furnace.signals["zone1"], 1_000_000_000, 40.0))
    assert controller.output == 50.0 + 2.0 * 10.0
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
    controller.regulate(50.0, transfer=Transfer.COLD)
    controller.on_reading(Reading(furnace.signals["zone1"], 1_000_000_000, 30.0))
    assert controller.output == 500.0 + 100.0 * 20.0
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
    assert controller.measured is not None and controller.measured.value == 9.0
    assert len(writes) == before + 1

    clock.advance(0.1)
    controller.on_reading(Reading(zone1, clock.now_ns(), 5.0))
    assert len(writes) == before + 2, "a reading past the period steps again"


def test_manual_holds_the_demand_and_regulate_resumes_bumplessly(furnace):
    clock = SteppedClock(0)
    controller, writes = _regulating(furnace, clock, law=PI(kp=1.0, ki=0.5))
    zone1 = furnace.signals["zone1"]
    controller.on_reading(Reading(zone1, 0, 40.0))
    controller.regulate(50.0, transfer=Transfer.COLD)
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
    reset = controller.regulate(50.0, transfer=Transfer.COLD)
    assert reset.output == 50.0 and reset.bump == pytest.approx(50.0 - held)


@pytest.mark.parametrize("aim", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_aim_is_refused_before_anything_changes(furnace, aim):
    clock = SteppedClock(0)
    controller, writes = _regulating(furnace, clock, law=PI(kp=1.0, ki=0.5))
    zone1 = furnace.signals["zone1"]
    controller.on_reading(Reading(zone1, 0, 40.0))
    controller.regulate(50.0)
    controller.on_reading(Reading(zone1, 1_000_000_000, 41.0))
    before = (controller.mode, controller.reference, controller.correction, len(writes))

    with pytest.raises(ValueError, match="not finite"):
        controller.regulate(aim)
    with pytest.raises(ValueError, match="not finite"):
        controller.set_setpoint(aim)
    assert (controller.mode, controller.reference, controller.correction, len(writes)) == before


def test_a_generator_with_a_non_finite_argument_is_refused():
    with pytest.raises(ValueError):
        TypeAdapter(GeneratorConfig).validate_python({"tag": "hold", "value": float("nan")})


def test_the_first_step_after_an_outage_counts_as_one_ordinary_step(furnace):
    clock = SteppedClock(0)
    law = PI(kp=0.0, ki=1.0)  # the output is the integral alone
    controller, writes = _regulating(furnace, clock, law=law)
    zone1 = furnace.signals["zone1"]
    controller.on_reading(Reading(zone1, 0, 40.0))
    controller.regulate(50.0, transfer=Transfer.COLD)
    for i in range(1, 6):  # a 10-degree error, one second apart: +10 a step
        controller.on_reading(Reading(zone1, i * 1_000_000_000, 40.0))
    before = law.integral

    # the source goes quiet for ten minutes, then reads again
    controller.on_reading(Reading(zone1, 605 * 1_000_000_000, 40.0))
    assert law.integral - before == pytest.approx(10.0), "one ordinary step, not 600 s of error"
    controller.on_reading(Reading(zone1, 606 * 1_000_000_000, 40.0))
    assert law.integral - before == pytest.approx(20.0), "and the steps after it are ordinary"


def test_a_slower_but_steady_source_is_not_an_outage(furnace):
    clock = SteppedClock(0)
    law = PI(kp=0.0, ki=1.0)
    controller, _ = _regulating(furnace, clock, law=law)
    zone1 = furnace.signals["zone1"]
    controller.on_reading(Reading(zone1, 0, 40.0))
    controller.regulate(50.0, transfer=Transfer.COLD)
    t = 0
    for step_s in (1, 1, 2, 2, 2):  # the interval widens, never more than threefold at once
        t += step_s * 1_000_000_000
        controller.on_reading(Reading(zone1, t, 40.0))
    assert law.integral == pytest.approx(10.0 * 8), "every second of error counted"


def test_linear_ramp_setpoint_config_round_trips_and_builds():
    assert get_catalog().generators["linear_ramp_setpoint"] is LinearRampSetpoint
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


def test_a_ramp_starts_where_the_setpoint_is_and_lands_at_its_end(furnace):
    """Pinned: regulating at 20, a ramp to 100 over 60 s reads 20 at t=0, not near 100."""
    clock = SteppedClock(0)
    controller, _ = _regulating(furnace, clock, law=P(kp=1.0))
    zone1 = furnace.signals["zone1"]
    controller.on_reading(Reading(zone1, 0, 20.0))
    controller.regulate(20.0, transfer=Transfer.COLD)
    controller.regulate(20.0, generator=LinearRampSetpoint(Duration(60), 100.0))

    assert controller.setpoint == 20.0
    assert controller.setpoint_at(0) == 20.0 and controller.rate_at(0) == pytest.approx(80.0 / 60)
    assert controller.setpoint_at(30_000_000_000) == pytest.approx(60.0)
    assert controller.arrived is False
    clock.advance(60.0)
    assert (
        controller.setpoint_at(clock.now_ns()) == 100.0
        and controller.rate_at(clock.now_ns()) == 0.0
    )
    assert controller.arrived is True and controller.state.arrived is True

    # Descending: the pace says how fast, the span says which way.
    down = LinearRampSetpoint(Speed(10.0, TimeUnit.MINUTE), 40.0)
    controller.set_setpoint(100.0, generator=down)
    now = clock.now_ns()
    assert controller.rate_at(now) == pytest.approx(-10.0 / 60)
    assert controller.setpoint_at(now + 60_000_000_000) == pytest.approx(90.0)
    assert down.end_time == pytest.approx(60.0 + 360.0)

    # To where the setpoint already is: nothing to walk, so it has landed at once.
    flat = LinearRampSetpoint(Speed(10.0, TimeUnit.MINUTE), 100.0)
    controller.set_setpoint(100.0, generator=flat)
    assert flat.finished(clock.from_start_s(clock.now_ns())) is True
    assert controller.arrived is True and controller.rate_at(clock.now_ns()) == 0.0


def test_a_negative_pace_is_refused():
    """`Speed` is positive by construction: a sign on the pace would fight the span's."""
    with pytest.raises(ValueError, match="rate must be positive"):
        Speed(-10.0, TimeUnit.MINUTE)
    with pytest.raises(Exception, match="greater than 0"):
        LinearRampSetpoint.config.model_validate({
            "tag": "linear_ramp_setpoint",
            "pace": {"per_minute": -10},
            "end": 30.0,
        })


def test_arrived_follows_the_reference(furnace):
    clock = SteppedClock(0)
    controller, _ = _regulating(furnace, clock, law=P(kp=1.0))
    assert controller.arrived is False, "nothing to have arrived at"
    controller.on_reading(Reading(furnace.signals["zone1"], 0, 20.0))
    controller.regulate(50.0, transfer=Transfer.COLD)
    assert controller.arrived is True and controller.view.arrived is True

    class Endless(SetPointGenerator):
        def __init__(self) -> None:
            pass

        def generate(self, time: float) -> float:
            return 1.0

    controller.set_setpoint(50.0, generator=Endless())
    assert controller.arrived is False, "the base finished() is never"


def test_hold_is_a_fixed_setpoint_that_may_end():
    assert get_catalog().generators["hold"] is Hold
    forever = Hold.config.model_validate({"tag": "hold", "value": 30.0}).build()
    assert isinstance(forever, Hold) and forever.bounded is False
    forever.start(10.0, 20.0)
    assert forever.generate(10.0) == 30.0 and forever.generate(1e9) == 30.0
    assert forever.end_time is None and forever.finished(1e9) is False
    assert forever.rate(10.0) == 0.0
    assert TypeAdapter(Hold).dump_python(forever, mode="json") == {
        "tag": "hold",
        "value": 30.0,
        "duration": None,
    }

    soak = Hold(30.0, Duration(300))
    assert soak.bounded is True
    soak.start(10.0, 20.0)
    assert soak.end_time == 310.0
    assert soak.finished(309.9) is False and soak.finished(310.0) is True
    assert TypeAdapter(Hold).dump_python(soak, mode="json")["end_time"] == 310.0


def test_profile_runs_its_segments_back_to_back():
    assert get_catalog().generators["profile"] is Profile
    config = Profile.config.model_validate({
        "tag": "profile",
        "segments": [
            {"tag": "linear_ramp_setpoint", "pace": {"per_minute": 10}, "end": 30.0},
            {"tag": "hold", "value": 30.0, "duration": {"minutes": 5}},
            {"tag": "linear_ramp_setpoint", "pace": {"seconds": 60}, "end": 0.0},
            {"tag": "hold", "value": 0.0},
        ],
    })
    profile = config.build()
    assert isinstance(profile, Profile) and profile.bounded is False
    assert profile.active is None, "not yet asked"
    profile.start(100.0, 20.0)
    assert [g.end_time for g in profile.generators] == [160.0, 460.0, 520.0, None]
    assert profile.end_time is None

    # Each segment starts where the last landed: 20 -> 30 over 60 s, soak, 30 -> 0 over 60 s.
    assert profile.generate(100.0) == pytest.approx(20.0) and profile.active == 0
    assert profile.rate(130.0) == pytest.approx(10.0 / 60)
    assert profile.generate(130.0) == pytest.approx(25.0)
    assert profile.generate(200.0) == 30.0 and profile.active == 1 and profile.rate(200.0) == 0.0
    assert profile.generate(490.0) == pytest.approx(15.0) and profile.active == 2
    assert profile.rate(490.0) == pytest.approx(-0.5)
    assert profile.finished(519.9) is False
    assert profile.generate(1e6) == 0.0 and profile.active == 3
    assert profile.finished(1e6) is False, "the last segment is endless"

    wire = TypeAdapter(Profile).dump_python(profile, mode="json")
    assert wire["tag"] == "profile" and wire["active"] == 3 and "end_time" not in wire
    assert [segment["tag"] for segment in wire["segments"]] == [
        "linear_ramp_setpoint",
        "hold",
        "linear_ramp_setpoint",
        "hold",
    ]
    assert wire["segments"][1] == {
        "tag": "hold",
        "value": 30.0,
        "duration": {"seconds": 300, "nanoseconds": 0},
    }


def test_a_bounded_profile_finishes_with_its_last_segment_and_nests():
    inner = Profile.config(
        segments=[
            LinearRampSetpoint.config(pace=Duration(10), end=10.0),
            Hold.config(value=10.0, duration=Duration(10)),
        ]
    )
    outer = Profile([inner, LinearRampSetpoint.config(pace=Duration(10), end=0.0)])
    assert outer.bounded is True
    outer.start(0.0, 0.0)
    assert outer.end_time == 30.0
    assert outer.generate(5.0) == pytest.approx(5.0) and outer.active == 0
    assert outer.generate(15.0) == 10.0
    assert outer.generate(25.0) == pytest.approx(5.0) and outer.active == 1
    assert outer.finished(29.9) is False and outer.finished(30.0) is True
    assert outer.generate(31.0) == 0.0

    union = TypeAdapter(GeneratorConfig)
    nested = union.validate_python({
        "tag": "profile",
        "segments": [
            {"tag": "profile", "segments": [{"tag": "hold", "value": 1.0, "duration": 1}]},
            {"tag": "hold", "value": 2.0},
        ],
    })
    assert isinstance(nested, Profile.config) and isinstance(nested.build(), Profile)
    assert set(union.json_schema()["$defs"]) >= {
        "HoldConfig",
        "LinearRampSetpointConfig",
        "ProfileConfig",
    }


def test_a_profile_refuses_an_endless_segment_before_the_last_and_no_segments():
    with pytest.raises(
        ValueError,
        match=r"profile segment 0 \(hold\) never ends, so segment 1 would never start",
    ):
        Profile([Hold.config(value=1.0), Hold.config(value=2.0, duration=Duration(1))])
    with pytest.raises(ValueError, match="at least one segment"):
        Profile([])
    with pytest.raises(Exception, match="at least 1"):
        Profile.config.model_validate({"tag": "profile", "segments": []})
