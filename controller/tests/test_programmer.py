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
    programmer.start(Note("after"))  # not "already running"
    assert seen == ["after"]


def test_steps_after_a_wait_run_on_the_worker_and_a_failure_there_is_an_event(rig, note):
    Note, seen = note
    programmer = Programmer(rig)
    programmer.start(Program([Note("a"), Wait("go", name="go"), Note("b"), Note("boom")], name="p"))
    assert seen == ["a"] and programmer.state.step == 1 and programmer.state.command == "wait"
    assert "go" in rig.signals.states()
    rig.signals.fire("go")
    programmer.join(2)
    assert seen == ["a", "b"] and programmer.running is False
    # The programmer also narrates: started, one `step` per step, finished.
    kinds = [e.kind for e in rig.recent]
    assert kinds[0] == "started" and kinds[-1] == "finished" and kinds.count("step") == 4
    (event,) = [e for e in rig.recent if e.level == Level.ERROR]
    assert event.scope == "program" and event.subject == "p[3]"
    assert event.kind == "step_failed" and event.details["error"] == "RuntimeError: no such thing"


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
