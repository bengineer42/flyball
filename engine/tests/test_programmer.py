"""The programmer: atomic steps apply at once, waits go to the worker, failures are events."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from flyball.foundation.device import Committable, Demand, Readout, Sample, Severity, command
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Watt
from flyball.sequencing import Program, Programmer, Prompt, Step
from flyball.sequencing.errors import ProgramAlreadyRunningError

TEMP = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)


class Heater(Committable):
    """One output zone and one power demand, plus a command independent of any controller."""

    zone = Readout("zone", "", TEMP)
    power = Demand("power", "", POWER, limits=(0.0, 100.0))

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.duty = 0.0

    @command
    def set_duty(self, duty: float) -> float:
        """Drive the element directly, independent of any controller."""
        self.duty = duty
        return duty


@pytest.fixture
def note(fresh):
    seen = []
    tag = fresh("note")

    @dataclass(frozen=True)
    class Note(Step, tag=tag, primary="text"):
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
    # silent `succeeded` one: an ERROR event, and `state` says so until the next start.
    codes = [e.code for e in rig.recent]
    assert codes == ["started", "step", "step_failed", "failed"]
    state = programmer.state
    assert state.running is False and state.failed is True
    assert state.error is not None and "no such thing" in state.error
    programmer.start(Note("after"))  # not "already running"
    assert seen == ["after"]
    assert programmer.state.failed is False, "starting again clears the previous failure"


def test_steps_after_a_prompt_run_on_the_worker_and_a_failure_there_is_an_event(rig, note):
    Note, seen = note
    programmer = Programmer(rig)
    programmer.start(
        Program([Note("a"), Prompt("go", name="go"), Note("b"), Note("boom")], name="p")
    )
    assert seen == ["a"] and programmer.state.step == 1 and programmer.state.command == "prompt"
    assert "go" in rig.triggers.states()
    assert rig.triggers.states()["go"].prompt is True, "a prompt is answered by a person"
    rig.triggers.fire("go")
    programmer.join(2)
    assert seen == ["a", "b"] and programmer.running is False
    # The programmer also narrates: started, one `step` per step, the step that
    # raised, and a `failed` finish rather than a `succeeded` one.
    codes = [e.code for e in rig.recent]
    assert codes[0] == "started" and codes[-1] == "failed" and codes.count("step") == 4
    (step_event, finish_event) = [e for e in rig.recent if e.severity == Severity.ERROR]
    assert step_event.scope == "program" and step_event.subject == "p[3]"
    assert (
        step_event.code == "step_failed"
        and step_event.details["error"] == "RuntimeError: no such thing"
    )
    assert finish_event.scope == "program" and finish_event.subject == "p"
    assert finish_event.code == "failed" and "no such thing" in finish_event.message
    state = programmer.state
    assert state.running is False and state.failed is True and "no such thing" in state.error


def test_a_later_step_naming_a_missing_controller_fails_the_program_without_running_what_follows(
    rig, note, fresh
):
    """The bug: `regulate` on a real controller, then a typo'd one, used to finish clean."""
    from flyball.control import P
    from flyball.rig import ControllerNotFoundError
    from flyball.sequencing.loops import Regulate

    Note, seen = note
    heater = Heater(fresh("heater"))
    rig.add_device(heater)
    controller = rig.attach_controller(
        heater.signals["power"], heater.signals["zone"], law=P(kp=1.0), default=True
    )
    rig.on_samples([Sample(heater.root, rig.clock.now_ns(), {heater.signals["zone"]: 20.0})])

    programmer = Programmer(rig)
    program = Program(
        [
            Regulate(setpoint=30.0, controllers=controller.name),  # applies fine
            Regulate(setpoint=10.0, controllers="no_such_controller"),  # step 1: fails
            Note("never"),  # step 2: must not run
        ],
        name="p2",
    )
    with pytest.raises(ControllerNotFoundError, match="no_such_controller"):
        programmer.start(program)
    assert seen == [] and programmer.running is False
    assert controller.reference is not None  # step 0 did apply
    codes = [e.code for e in rig.recent]
    assert codes == ["started", "step", "step", "step_failed", "failed"]
    (step_event,) = [e for e in rig.recent if e.code == "step_failed"]
    assert step_event.subject == "p2[1]" and "no_such_controller" in step_event.message
    state = programmer.state
    assert state.failed is True and "no_such_controller" in state.error


def test_cancel_stops_at_the_prompt_and_start_can_replace_a_running_program(rig, note):
    Note, seen = note
    programmer = Programmer(rig)
    programmer.start(Program([Prompt("one", name="one"), Note("never")], name="first"))
    with pytest.raises(ProgramAlreadyRunningError):
        programmer.start(Note("x"))
    programmer.start(Note("instead"), cancel=True)
    assert seen == ["instead"] and rig.triggers.states() == {}
    assert programmer.running is False
    ended = [(e.subject, e.code) for e in rig.recent if e.code in ("cancelled", "succeeded")]
    assert ended == [("first", "cancelled"), ("program", "succeeded")]


def test_a_program_ends_succeeded_cancelled_or_interrupted_with_a_reason(rig, note):
    Note, seen = note
    programmer = Programmer(rig)
    programmer.start(Note("done"))
    assert rig.recent[-1].code == "succeeded"
    programmer.start(Program([Prompt("one", name="one")]))
    programmer.cancel()
    assert rig.recent[-1].code == "cancelled"
    programmer.start(Program([Prompt("two", name="two")]))
    programmer.interrupt("the rig was stopped")
    event = rig.recent[-1]
    assert event.code == "interrupted" and event.details["reason"] == "the rig was stopped"
    assert "the rig was stopped" in event.message
    programmer.start(Program([Prompt("three", name="three"), Note("never")]))
    rig.triggers.interrupt("three")  # a person cancels what it waits on
    programmer.join(2)
    assert rig.recent[-1].code == "cancelled" and "never" not in seen


def test_an_interrupt_while_a_step_applies_cancels_the_activity_it_returns(rig, fresh):
    """An interrupt between `_work`'s `_abort` check and `_apply` publishing the activity.

    It used to cancel the previous (settled) activity, then join a worker waiting
    forever on the new one.
    """
    import threading

    from flyball.sequencing import Activity

    class Watched(Programmer):
        """Says when `interrupt` has set `_abort`, so the test needs no sleeps."""

        aborted = threading.Event()

        @property
        def _abort(self) -> bool:  # type: ignore[override]
            return self.__dict__.get("_abort_value", False)

        @_abort.setter
        def _abort(self, value: bool) -> None:
            self.__dict__["_abort_value"] = value
            if value:
                self.aborted.set()

    programmer = Watched(rig)
    programmer.aborted = threading.Event()
    entered = threading.Event()
    returned: list[Activity] = []

    @dataclass(frozen=True)
    class Slow(Step, tag=fresh("slow")):
        """Applies only once the interrupt has been asked for, then returns a wait."""

        def run(self, rig, operator=None):
            entered.set()
            assert programmer.aborted.wait(5), "interrupt never set _abort"
            returned.append(activity := Activity())  # no timeout: waits until settled
            return activity

    programmer.start(Program([Prompt("go", name="go"), Slow()]))
    rig.triggers.fire("go")
    assert entered.wait(5), "the worker never reached the second step"
    interrupter = threading.Thread(target=programmer.interrupt, args=("a test",), daemon=True)
    interrupter.start()
    try:
        interrupter.join(5)
        assert not interrupter.is_alive(), "interrupt hung joining a worker waiting on its step"
        assert returned[0].interrupted and programmer.running is False
        assert [e.code for e in rig.recent][-1] == "interrupted"
    finally:  # unwind a hung worker so the failure does not leak a thread
        for activity in returned:
            activity.interrupt()
        interrupter.join(5)


def test_a_timed_out_prompt_ends_the_program(rig, note):
    from flyball.foundation.time import Duration

    Note, seen = note
    programmer = Programmer(rig)
    programmer.start(Program([Prompt("brief", timeout=Duration(0.05)), Note("after")]))
    programmer.join(2)
    assert seen == [] and programmer.running is False
    (event,) = [e for e in rig.recent if e.severity == Severity.WARNING]
    assert event.code == "step_timed_out"
    assert rig.recent[-1].code == "failed", "a program that gave up did not succeed"
    assert programmer.state.failed and "gave up after" in (programmer.state.error or "")


def test_arrive_waits_for_a_subset_of_controllers_and_ramp_can_be_non_blocking():
    """`ramp wait: false` returns at once; `settle` fires only when the named controllers settle."""
    import time

    from flyball.control import P
    from flyball.foundation.time import Duration
    from flyball.rig import Rig
    from flyball.sequencing import Program, Programmer
    from flyball.sequencing.loops import Ramp, Regulate, Settle

    rig = Rig()
    a, b = Heater("ha"), Heater("hb")
    rig.add_device(a)
    rig.add_device(b)
    ca = rig.attach_controller(a.signals["power"], a.signals["zone"], law=P(kp=1.0), default=True)
    cb = rig.attach_controller(b.signals["power"], b.signals["zone"], law=P(kp=1.0))
    programmer = Programmer(rig)

    def deliver(device: Heater, value: float) -> None:
        rig.on_samples([Sample(device.root, rig.clock.now_ns(), {device.signals["zone"]: value})])

    deliver(a, 20.0)
    deliver(b, 20.0)
    program = Program(
        [
            Regulate(setpoint=50.0, controllers=[ca.name, cb.name]),
            Ramp(to=60.0, pace=Duration(0.01), controllers=[ca.name], wait=False),
            Settle(controllers=[ca.name], within=0.5, count=2),
        ],
        name="settle-test",
    )
    programmer.start(program)
    assert programmer.running and programmer.state.command == "settle"  # the ramp did not block
    assert "settle:" + ca.name in rig.triggers.states()
    deliver(b, 50.0)  # hb settling is irrelevant to a settle on ha's controller
    deliver(b, 50.0)
    assert programmer.running
    time.sleep(0.02)  # the ramp reaches 60 in 10 ms of rig time
    deliver(a, 60.2)
    assert programmer.running  # one reading within band: not yet
    deliver(a, 59.8)
    programmer.join(2)
    assert programmer.running is False


def test_a_timed_wait_is_an_activity_but_not_a_prompt(rig, note):
    from flyball.foundation.time import Duration
    from flyball.sequencing.loops import Wait

    Note, seen = note
    programmer = Programmer(rig)
    programmer.start(Program([Wait(Duration(60)), Note("after")]))
    ((name, state),) = rig.triggers.states().items()
    assert name == "wait", "a timed wait registers under its tag"
    assert state.prompt is False, "a timed wait ends on its own; nobody should be asked"
    programmer.start(Note("instead"), cancel=True)


def test_a_timed_wait_can_time_out_like_a_prompt(rig, note):
    from flyball.foundation.time import Duration
    from flyball.sequencing.loops import Wait

    Note, seen = note
    programmer = Programmer(rig)
    programmer.start(Program([Wait(Duration(60), timeout=Duration(0.05)), Note("after")]))
    programmer.join(2)
    assert seen == [] and programmer.running is False
    (event,) = [e for e in rig.recent if e.severity == Severity.WARNING]
    assert event.code == "step_timed_out"


def test_regulate_names_a_controller_by_its_target_address(rig, fresh):
    from flyball.control import P
    from flyball.sequencing.loops import Regulate

    heater = Heater(fresh("heater"))
    rig.add_device(heater)
    controller = rig.attach_controller(
        heater.signals["power"], heater.signals["zone"], law=P(kp=2.0)
    )
    Regulate(setpoint=42.0, controllers=heater.signals["power"].address).run(rig)
    assert controller.reference == 42.0 and controller.mode.active()


def test_a_set_step_lands_as_a_demand(rig, fresh):
    from flyball.sequencing.devices import Set

    heater = Heater(fresh("heater"))
    rig.add_device(heater)
    Set(device=heater.name, values={"power": 42.0}).run(rig)
    assert heater.written[heater.signals["power"]].value == 42.0


def test_a_set_step_refuses_a_signal_a_controller_drives(rig, fresh):
    from flyball.control import P
    from flyball.foundation.errors import ConflictError
    from flyball.sequencing.devices import Set

    heater = Heater(fresh("heater"))
    rig.add_device(heater)
    controller = rig.attach_controller(
        heater.signals["power"], heater.signals["zone"], law=P(kp=1.0)
    )
    Set(device=heater.name, values={"power": 5.0}).run(rig)  # manual: the step may set it
    controller.regulate(50.0)
    with pytest.raises(ConflictError):
        Set(device=heater.name, values={"power": 5.0}).run(rig)


def test_a_set_step_refuses_a_bare_signal_address(rig, fresh):
    from flyball.foundation.errors import NotFoundError
    from flyball.sequencing.devices import Set

    heater = Heater(fresh("heater"))
    rig.add_device(heater)
    with pytest.raises(NotFoundError):
        Set(device=f"{heater.name}.power", values={"power": 1.0}).run(rig)


def test_a_command_step_calls_a_device_s_own_command(rig, fresh):
    from flyball.foundation.errors import NotFoundError
    from flyball.sequencing import RunCommand

    heater = Heater(fresh("heater"))
    rig.add_device(heater)
    RunCommand(device_command="set_duty", device=heater.name, args={"duty": 0.5}).run(rig)
    assert heater.duty == 0.5

    with pytest.raises(NotFoundError, match="nowhere"):
        RunCommand(device_command="set_duty", device="nowhere", args={"duty": 1.0}).run(rig)

    with pytest.raises(NotFoundError, match="nope"):
        RunCommand(device_command="nope", device=heater.name).run(rig)


def test_missing_names_a_controller_the_rig_lacks_or_has_no_default(rig, fresh):
    from flyball.control import P
    from flyball.foundation.time import Duration
    from flyball.sequencing.loops import Manual, Ramp, Regulate, Settle

    heater = Heater(fresh("heater"))
    rig.add_device(heater)

    for named_none in (Regulate(setpoint=1.0), Manual(), Settle(), Ramp(to=1.0, pace=Duration(1))):
        assert named_none.missing(rig) == ["the rig has no default controller"]

    # the first controller attached becomes the default (`Controllers.add`)
    controller = rig.attach_controller(
        heater.signals["power"], heater.signals["zone"], law=P(kp=1.0)
    )

    for named_unknown in (
        Regulate(setpoint=1.0, controllers="no_such"),
        Manual(controllers="no_such"),
        Settle(controllers="no_such"),
        Ramp(to=1.0, pace=Duration(1), controllers="no_such"),
    ):
        assert named_unknown.missing(rig) == ["controller 'no_such' is not on the rig"]

    assert Regulate(setpoint=1.0, controllers=controller.name).missing(rig) == []
    assert Regulate(setpoint=1.0).missing(rig) == []


def test_regulate_missing_also_names_an_unstored_tuning(rig, fresh):
    from flyball.control import P
    from flyball.library.tunings import Tuning
    from flyball.sequencing.loops import Regulate

    heater = Heater(fresh("heater"))
    rig.add_device(heater)
    rig.attach_controller(
        heater.signals["power"], heater.signals["zone"], law=P(kp=1.0), default=True
    )
    rig.tunings.add(Tuning(name="brisk", config=P(kp=4.0).config))

    assert Regulate(setpoint=1.0, tuning="brisk").missing(rig) == []
    assert Regulate(setpoint=1.0, tuning="ghost").missing(rig) == ["tuning 'ghost' is not stored"]
    assert Regulate(setpoint=1.0, controllers="no_such", tuning="ghost").missing(rig) == [
        "controller 'no_such' is not on the rig",
        "tuning 'ghost' is not stored",
    ]


def test_run_command_missing_names_an_unknown_device_or_command(rig, fresh):
    from flyball.sequencing import RunCommand

    heater = Heater(fresh("heater"))
    rig.add_device(heater)
    assert RunCommand(device_command="set_duty", device=heater.name).missing(rig) == []
    assert RunCommand(device_command="set_duty", device="ghost").missing(rig) == [
        "device 'ghost' is not on the rig"
    ]
    assert RunCommand(device_command="nope", device=heater.name).missing(rig) == [
        f"{heater.name!r} has no command 'nope'"
    ]


def test_set_missing_names_the_address_that_fails(rig, fresh):
    from flyball.sequencing.devices import Set

    heater = Heater(fresh("heater"))
    rig.add_device(heater)
    assert Set(device=heater.name, values={"power": 1.0}).missing(rig) == []
    assert Set(device="ghost", values={"power": 1.0}).missing(rig) == [
        "Address 'ghost' not found: no device 'ghost'"
    ]
    assert Set(device=heater.name, values={"nope": 1.0}).missing(rig) == [
        f"Address '{heater.name}.nope' not found: no 'nope' under {heater.name}"
    ]
    assert Set(device=heater.name, values={"zone": 1.0}).missing(rig) == [
        f"'{heater.name}.zone' [rp] is not writable"
    ]


def test_program_missing_collects_gaps_by_step_index(rig, fresh):
    from flyball.sequencing import Program, RunCommand
    from flyball.sequencing.loops import Regulate

    heater = Heater(fresh("heater"))
    rig.add_device(heater)
    program = Program([
        Regulate(setpoint=1.0),  # step 0: no default controller
        RunCommand(device_command="set_duty", device=heater.name),  # step 1: fine
        RunCommand(device_command="nope", device="ghost"),  # step 2: unknown device
    ])
    assert program.missing(rig) == {
        0: "the rig has no default controller",
        2: "device 'ghost' is not on the rig",
    }
