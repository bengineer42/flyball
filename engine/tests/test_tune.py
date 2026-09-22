"""The `tune:` command: as a program step, as a one-shot, and when interrupted.

A controller is wired to a simulated plant and fed readings from the test
thread; the programmer's worker sits in `activity.wait()` meanwhile, which is
what it does on a real rig while the poller publishes. The plant is the one
`test_autotune.py` fits directly, so a wrong answer here is the command's
fault, not the fitter's.
"""

from __future__ import annotations

import pytest
from flyball_sim.plant import Fopdt

from flyball.control.laws import PI
from flyball.foundation.device import Access, Device, Reading, SignalSpec
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius
from flyball.foundation.time import Duration
from flyball.model.controller import Controller, ControllerMode
from flyball.sequencing import Program, Programmer
from flyball.sequencing.loops import Regulate
from flyball.sequencing.tuning import Tune, Tuned

TEMP = Quantity("temperature", Celsius)
DT = 1.0


class Oven(Device):
    """One sensed zone and one setpoint-shaped demand: a smart drive, both in C."""

    TREE = (
        SignalSpec(name="zone", quantity=TEMP, access=Access.RP, range=(0.0, 200.0)),
        SignalSpec(name="target", quantity=TEMP, access=Access.W, limits=(0.0, 200.0)),
    )


@pytest.fixture
def oven() -> Oven:
    return Oven("oven")


@pytest.fixture
def plant() -> Fopdt:
    """Tau 60 s, dead 5 s, gain 80 over ambient 20, resting at 50 C."""
    model = Fopdt(60.0, 5.0, gain=80.0, value=50.0, ambient=20.0)
    model.input = model.feedforward(50.0)
    return model


@pytest.fixture
def loop(rig, oven, plant, clock):
    """A controller whose demand drives `plant` through its own feedforward."""

    def write(demand: float) -> float:
        plant.input = plant.feedforward(demand)
        return demand

    controller = Controller(clock, oven.signals["target"], oven.signals["zone"], write=write)
    rig.controllers.add(controller, default=True)
    return controller


def prime(rig, controller, plant) -> None:
    """Publish one reading, as a poller would have before any programme started."""
    rig.clock.advance(DT)
    controller.on_reading(Reading(controller.source, rig.clock.now_ns(), plant.output))


def feed(rig, controller, plant, programmer, limit: int = 20_000) -> int:
    """Publish readings until the programmer stops; return how many it took."""
    for count in range(limit):
        if not programmer.running:
            return count
        rig.clock.advance(DT)
        controller.on_reading(Reading(controller.source, rig.clock.now_ns(), plant.output))
        plant.step(DT)
    raise AssertionError(f"the programme did not finish in {limit} readings")


# region As a program step


def test_a_tune_step_fits_the_plant_and_stores_the_gains(rig, loop, plant, clock):
    prime(rig, loop, plant)
    programmer = Programmer(rig)
    programmer.start(
        Program([
            Tune(
                loop="oven.target",
                save_as="fitted",
                size=10.0,
                window=Duration(seconds=40),
                band=0.05,
            )
        ])
    )
    feed(rig, loop, plant, programmer)

    assert rig.tunings.get("fitted") is not None
    law = rig.tunings.get("fitted").build()
    assert isinstance(law, PI)
    # A smart drive inverts the plant, so the loop sees unit gain; IMC on a
    # tau 60 / dead 5 plant asks for roughly kp = ti / (gain * (lam + theta/2)).
    assert 0.5 < law.kp < 2.0
    assert law.ki > 0.0


def test_the_fitted_model_matches_the_plant(rig, loop, plant, clock):
    """What the command measured, not just that it measured something."""
    prime(rig, loop, plant)
    command = Tune(
        loop="oven.target", save_as="fitted", size=10.0, window=Duration(seconds=40), band=0.05
    )
    activity = command.run(rig)
    assert isinstance(activity, Tuned)
    activity.attach(rig)
    for _ in range(20_000):
        if activity.fired:
            break
        rig.clock.advance(DT)
        loop.on_reading(Reading(loop.source, rig.clock.now_ns(), plant.output))
        plant.step(DT)
    activity.detach(rig)

    assert activity.model is not None
    assert activity.model.gain == pytest.approx(1.0, rel=0.05)
    assert activity.model.tau == pytest.approx(60.0, rel=0.1)
    assert activity.model.dead_time == pytest.approx(5.0, abs=2.0)


def test_a_following_regulate_can_name_what_tune_stored(rig, loop, plant, clock):
    prime(rig, loop, plant)
    programmer = Programmer(rig)
    program = Program([
        Tune(
            loop="oven.target", save_as="fitted", size=10.0, window=Duration(seconds=40), band=0.05
        ),
        Regulate(setpoint=55.0, loop="oven.target", tuning="fitted"),
    ])
    programmer.start(program)
    feed(rig, loop, plant, programmer)

    assert loop.mode is ControllerMode.REGULATING
    assert isinstance(loop.law, PI)
    assert loop.reference == 55.0


# region As a one-shot


def test_tune_runs_as_a_single_command_through_the_programmer(rig, loop, plant, clock):
    """`POST /api/programs/command` does exactly this: one Command, no Program."""
    prime(rig, loop, plant)
    programmer = Programmer(rig)
    programmer.start(
        Tune(
            loop="oven.target", save_as="oneshot", size=10.0, window=Duration(seconds=40), band=0.05
        )
    )
    feed(rig, loop, plant, programmer)
    assert rig.tunings.get("oneshot") is not None


# region Putting the loop back


def test_an_interrupted_tune_restores_the_loop_it_found(rig, loop, plant, clock):
    """The case that matters on a furnace: stop half way, do not leave it open-loop."""
    prime(rig, loop, plant)
    loop.regulate(50.0, tuning=PI(kp=0.5, ki=0.01))
    was = loop.law

    programmer = Programmer(rig)
    programmer.start(
        Tune(
            loop="oven.target", save_as="fitted", size=10.0, window=Duration(seconds=40), band=0.05
        )
    )
    for _ in range(30):  # part way in: the first plateau is not reached yet
        rig.clock.advance(DT)
        loop.on_reading(Reading(loop.source, rig.clock.now_ns(), plant.output))
        plant.step(DT)
    programmer.interrupt()

    assert rig.tunings.get("fitted") is None
    assert loop.mode is ControllerMode.REGULATING
    assert loop.law is was
    assert loop.reference == 50.0


def test_a_tune_of_a_manual_loop_leaves_it_manual(rig, loop, plant, clock):
    prime(rig, loop, plant)
    programmer = Programmer(rig)
    programmer.start(
        Tune(
            loop="oven.target", save_as="fitted", size=10.0, window=Duration(seconds=40), band=0.05
        )
    )
    rig.clock.advance(DT)
    loop.on_reading(Reading(loop.source, rig.clock.now_ns(), plant.output))
    programmer.interrupt()
    assert loop.mode is ControllerMode.MANUAL


# region Defaults and refusals


def test_the_step_defaults_to_a_tenth_of_the_source_range(rig, loop, plant, clock):
    """`zone` spans 0..200, so a tenth is 20, and 50 C is nearer the bottom: +20."""
    prime(rig, loop, plant)
    activity = Tune(loop="oven.target").run(rig)
    assert isinstance(activity, Tuned)
    assert activity.test.size == pytest.approx(20.0)


def test_the_step_is_directed_away_from_the_nearer_end(rig, loop, plant, clock):
    loop.on_reading(Reading(loop.source, rig.clock.now_ns(), 180.0))
    activity = Tune(loop="oven.target").run(rig)
    assert isinstance(activity, Tuned)
    assert activity.test.size == pytest.approx(-20.0)


def test_the_base_defaults_to_the_current_reading(rig, loop, plant, clock):
    loop.on_reading(Reading(loop.source, rig.clock.now_ns(), 63.0))
    activity = Tune(loop="oven.target").run(rig)
    assert isinstance(activity, Tuned)
    assert activity.test.base == pytest.approx(63.0)


def test_a_loop_with_no_reading_yet_cannot_default_its_base(rig, loop, plant, clock):
    with pytest.raises(ValueError, match="no reading yet"):
        Tune(loop="oven.target").run(rig)


def test_the_band_defaults_to_a_twentieth_of_the_step(rig, loop, plant, clock):
    loop.on_reading(Reading(loop.source, rig.clock.now_ns(), 50.0))
    activity = Tune(loop="oven.target", size=10.0).run(rig)
    assert isinstance(activity, Tuned)
    assert activity.test._steady.band == pytest.approx(0.5)


def test_an_unknown_rule_is_refused_before_the_rig_moves(rig, loop, plant, clock):
    loop.on_reading(Reading(loop.source, rig.clock.now_ns(), 50.0))
    with pytest.raises(ValueError, match="unknown tuning rule"):
        Tune(loop="oven.target", rule="cohen-coon").run(rig)
    assert loop.mode is ControllerMode.MANUAL


def test_a_zero_step_is_refused(rig, loop, plant, clock):
    loop.on_reading(Reading(loop.source, rig.clock.now_ns(), 50.0))
    with pytest.raises(ValueError, match="cannot be zero"):
        Tune(loop="oven.target", size=0.0).run(rig)


def test_missing_reports_a_controller_the_rig_lacks(rig, loop):
    assert Tune(loop="nowhere.target").missing(rig) == [
        "controller 'nowhere.target' is not on the rig"
    ]
    assert Tune(loop="oven.target").missing(rig) == []


def test_the_command_is_registered_for_program_files():
    from flyball.model.catalog import get_catalog

    assert get_catalog().commands["tune"] is Tune
