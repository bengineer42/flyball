"""The programmer: atomic steps apply at once, waits go to the worker, failures are events."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from flyball.core.device import Level
from flyball.programmer import Command, Program, Programmer, Wait
from flyball.programmer.errors import ProgramAlreadyRunningError


@pytest.fixture
def note(fresh):
    seen = []
    tag = fresh("note")

    @dataclass(frozen=True)
    class Note(Command, tag=tag, primary="text"):
        """Append to a list."""

        text: str

        def run(self, rig, operator=None):
            if self.text == "boom":
                raise RuntimeError("no such thing")
            seen.append(self.text)
            return None

    return Note, seen


def test_atomic_program_runs_on_the_caller_and_is_done_on_return(rig, note):
    Note, seen = note
    programmer = Programmer(rig)
    programmer.start(Program([Note("a"), Note("b")]))
    assert seen == ["a", "b"] and programmer.running is False


def test_a_failing_first_step_raises_and_leaves_the_programmer_idle(rig, note):
    Note, seen = note
    programmer = Programmer(rig)
    with pytest.raises(RuntimeError, match="no such thing"):
        programmer.start(Program([Note("boom"), Note("never")]))
    assert seen == [] and programmer.running is False
    # A step failing on the calling thread is still a `failed` program, not a
    # silent `finished` one: an ERROR event, and `state` says so until the next start.
    kinds = [e.kind for e in rig.recent]
    assert kinds == ["started", "step", "step_failed", "failed"]
    state = programmer.state
    assert state.running is False and state.failed is True
    assert state.error is not None and "no such thing" in state.error
    programmer.start(Note("after"))  # not "already running"
    assert seen == ["after"]
    assert programmer.state.failed is False, "starting again clears the previous failure"


def test_steps_after_a_wait_run_on_the_worker_and_a_failure_there_is_an_event(rig, note):
    Note, seen = note
    programmer = Programmer(rig)
    programmer.start(Program([Note("a"), Wait("go", name="go"), Note("b"), Note("boom")], name="p"))
    assert seen == ["a"] and programmer.state.step == 1 and programmer.state.command == "wait"
    assert "go" in rig.signals.states()
    assert rig.signals.states()["go"].prompt is True, "a wait is answered by a person"
    rig.signals.fire("go")
    programmer.join(2)
    assert seen == ["a", "b"] and programmer.running is False
    # The programmer also narrates: started, one `step` per step, the step that
    # raised, and a `failed` finish rather than a `finished` one.
    kinds = [e.kind for e in rig.recent]
    assert kinds[0] == "started" and kinds[-1] == "failed" and kinds.count("step") == 4
    (step_event, finish_event) = [e for e in rig.recent if e.level == Level.ERROR]
    assert step_event.scope == "program" and step_event.subject == "p[3]"
    assert (
        step_event.kind == "step_failed"
        and step_event.details["error"] == "RuntimeError: no such thing"
    )
    assert finish_event.scope == "program" and finish_event.subject == "p"
    assert finish_event.kind == "failed" and "no such thing" in finish_event.message
    state = programmer.state
    assert state.running is False and state.failed is True and "no such thing" in state.error


def test_a_later_step_naming_a_missing_loop_fails_the_program_without_running_what_follows(
    rig, note, fresh
):
    """The bug this guards: `regulate` on a real loop then a typo'd one used to finish clean."""
    from flyball.control import P
    from flyball.core.reading import Measurand, Sample, Source
    from flyball.core.units.si import Celsius
    from flyball.programmer.loops import Regulate
    from flyball.runtime.loops import LoopNotFoundError
    from flyball.sim import RecordingActuator

    Note, seen = note
    temp = Measurand(fresh("temp"), Celsius)
    source = Source(fresh("source"), (temp,))
    heater = RecordingActuator(fresh("heater"))
    rig.attach_loop(source[temp], heater, law=P(kp=1.0), default=True)
    rig.on_read([Sample(source, 1, rig.clock.now_ns(), {temp: 20.0})])

    programmer = Programmer(rig)
    program = Program(
        [
            Regulate(setpoint=30.0, loop=heater.name),  # applies fine
            Regulate(setpoint=10.0, loop="no_such_loop"),  # step 1: fails
            Note("never"),  # step 2: must not run
        ],
        name="p2",
    )
    with pytest.raises(LoopNotFoundError, match="no_such_loop"):
        programmer.start(program)
    assert seen == [] and programmer.running is False
    assert rig.loops[heater.name].reference is not None  # step 0 did apply
    kinds = [e.kind for e in rig.recent]
    assert kinds == ["started", "step", "step", "step_failed", "failed"]
    (step_event,) = [e for e in rig.recent if e.kind == "step_failed"]
    assert step_event.subject == "p2[1]" and "no_such_loop" in step_event.message
    state = programmer.state
    assert state.failed is True and "no_such_loop" in state.error


def test_interrupt_stops_at_the_wait_and_start_can_replace_a_running_program(rig, note):
    Note, seen = note
    programmer = Programmer(rig)
    programmer.start(Program([Wait("one", name="one"), Note("never")]))
    with pytest.raises(ProgramAlreadyRunningError):
        programmer.start(Note("x"))
    programmer.start(Note("instead"), interrupt=True)
    assert seen == ["instead"] and rig.signals.states() == {}
    assert programmer.running is False


def test_a_timed_out_wait_ends_the_program(rig, note):
    from flyball.core.clock import Duration

    Note, seen = note
    programmer = Programmer(rig)
    programmer.start(Program([Wait("brief", timeout=Duration(0.05)), Note("after")]))
    programmer.join(2)
    assert seen == [] and programmer.running is False
    (event,) = [e for e in rig.recent if e.level == Level.WARNING]
    assert event.kind == "step_timed_out"


def test_arrive_waits_for_a_subset_of_loops_and_ramp_can_be_non_blocking():
    """`ramp wait: false` returns at once; `arrive` fires only when the named loops settle."""
    from flyball.control import P
    from flyball.core.clock import Duration
    from flyball.core.reading import Measurand, Source
    from flyball.core.units.si import Celsius
    from flyball.programmer import Program, Programmer
    from flyball.programmer.loops import Arrive, Ramp, Regulate
    from flyball.runtime.rig import Rig
    from flyball.sim import RecordingActuator

    class Heater(RecordingActuator):
        demand_unit = Celsius

    Measurand.forget("ar_temp")
    rig = Rig()
    temp = Measurand("ar_temp", Celsius)
    src_a, src_b = Source(f"ar_a_{id(rig)}", [temp]), Source(f"ar_b_{id(rig)}", [temp])
    a, b = Heater("ha"), Heater("hb")
    rig.attach_loop(src_a[temp], a, law=P(kp=1.0), default=True)
    rig.attach_loop(src_b[temp], b, law=P(kp=1.0))
    programmer = Programmer(rig)

    def deliver(source, value):
        from flyball.core.reading import Sample

        rig.on_read([Sample(source, 1, rig.clock.now_ns(), {temp: value})])

    deliver(src_a, 20.0)
    deliver(src_b, 20.0)
    program = Program(
        [
            Regulate(setpoint=50.0, loop=["ha", "hb"]),
            Ramp(to=60.0, pace=Duration(0.01), loop=["ha"], wait=False),
            Arrive(loop=["ha"], within=0.5, readings=2),
        ],
        name="arrive-test",
    )
    programmer.start(program)
    assert programmer.running and programmer.state.command == "arrive"  # the ramp did not block
    deliver(src_b, 50.0)  # hb settling is irrelevant to an arrive on ha
    deliver(src_b, 50.0)
    assert programmer.running
    import time

    time.sleep(0.02)  # the ramp reaches 60 in 10 ms of rig time
    deliver(src_a, 60.2)
    assert programmer.running  # one reading within band: not yet
    deliver(src_a, 59.8)
    programmer.join(2)
    assert programmer.running is False


def test_a_hold_is_a_signal_but_not_a_prompt(rig, note):
    from flyball.core.clock import Duration
    from flyball.programmer.loops import Hold

    Note, seen = note
    programmer = Programmer(rig)
    programmer.start(Program([Hold(Duration(60)), Note("after")]))
    (state,) = rig.signals.states().values()
    assert state.prompt is False, "a hold settles on its own; nobody should be asked"
    programmer.start(Note("instead"), interrupt=True)


def test_a_hold_can_time_out_like_a_wait(rig, note):
    from flyball.core.clock import Duration
    from flyball.programmer.loops import Hold

    Note, seen = note
    programmer = Programmer(rig)
    programmer.start(Program([Hold(Duration(60), timeout=0.05), Note("after")]))
    programmer.join(2)
    assert seen == [] and programmer.running is False
    (event,) = [e for e in rig.recent if e.level == Level.WARNING]
    assert event.kind == "step_timed_out"


def test_a_command_step_calls_a_device_s_own_command(rig, heater):
    from flyball.core.errors import NotFoundError
    from flyball.programmer import RunCommand
    from helpers import DutyHeater

    rig.add_actuator(heater)
    RunCommand(device_command="demand", actuator=heater.name, args={"demand": 42.0}).run(rig)
    assert heater.demands == [42.0] and heater.applied == 1, "the same commit the route makes"

    duty = DutyHeater(f"{heater.name}_duty")
    rig.add_actuator(duty)
    RunCommand(device_command="set_duty", actuator=duty.name, args={"duty": 0.5}).run(rig)
    assert duty.duty == 0.5

    with pytest.raises(NotFoundError):
        RunCommand(device_command="demand", actuator="nowhere", args={"demand": 1.0}).run(rig)


def test_a_command_step_refuses_demand_while_a_loop_regulates(rig, heater, probe, temperature):
    from flyball.control import P
    from flyball.core.errors import ConflictError
    from flyball.programmer import RunCommand

    rig.attach_loop(probe[temperature], heater, law=P(kp=1.0))
    rig.loops[heater.name].regulate(10.0)
    with pytest.raises(ConflictError):
        RunCommand(device_command="demand", actuator=heater.name, args={"demand": 5.0}).run(rig)


def test_regulate_swaps_in_a_stored_tuning_by_name_and_names_a_missing_one(rig, fresh):
    """The bug this guards: `tuning: gentle` used to hand the *name* to the loop."""
    from flyball.control import P
    from flyball.control.errors import TuningNotRegisteredError
    from flyball.core.reading import Measurand, Sample, Source
    from flyball.core.units.si import Celsius
    from flyball.programmer.loops import Regulate
    from flyball.sim import RecordingActuator

    temp = Measurand(fresh("temp"), Celsius)
    source = Source(fresh("source"), (temp,))
    heater = RecordingActuator(fresh("heater"))
    rig.attach_loop(source[temp], heater, law=P(kp=1.0), default=True)
    rig.on_read([Sample(source, 1, rig.clock.now_ns(), {temp: 20.0})])
    rig.tunings.add(P(kp=4.0).config.to_tuning("brisk"))

    programmer = Programmer(rig)
    programmer.start(Regulate(setpoint=30.0, loop=heater.name, tuning="brisk"))
    assert rig.loops[heater.name].law.kp == 4.0

    with pytest.raises(TuningNotRegisteredError, match="no_such_tuning"):
        programmer.start(Regulate(setpoint=30.0, loop=heater.name, tuning="no_such_tuning"))
