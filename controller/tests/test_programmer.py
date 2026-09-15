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
    (event,) = rig.recent
    assert event.level == Level.ERROR and event.scope == "program" and event.subject == "p[3]"
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
    (event,) = rig.recent
    assert event.kind == "step_timed_out" and event.level == Level.WARNING
