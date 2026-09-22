"""The programmer: atomic steps apply at once, waits go to the worker, failures are events."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from flyball.foundation.device import Committable, Demand, Level, Output, Sample, command
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Watt
from flyball.sequencing import Command, Program, Programmer, Wait
from flyball.sequencing.errors import ProgramAlreadyRunningError

TEMP = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)


class Heater(Committable):
    """One output zone and one power demand, plus a command independent of any controller."""

    zone = Output("zone", "", TEMP)
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
    assert "go" in rig.triggers.states()
    assert rig.triggers.states()["go"].prompt is True, "a wait is answered by a person"
    rig.triggers.fire("go")
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
            Regulate(setpoint=30.0, loop=controller.name),  # applies fine
            Regulate(setpoint=10.0, loop="no_such_controller"),  # step 1: fails
            Note("never"),  # step 2: must not run
        ],
        name="p2",
    )
    with pytest.raises(ControllerNotFoundError, match="no_such_controller"):
        programmer.start(program)
    assert seen == [] and programmer.running is False
    assert controller.reference is not None  # step 0 did apply
    kinds = [e.kind for e in rig.recent]
    assert kinds == ["started", "step", "step", "step_failed", "failed"]
    (step_event,) = [e for e in rig.recent if e.kind == "step_failed"]
    assert step_event.subject == "p2[1]" and "no_such_controller" in step_event.message
    state = programmer.state
    assert state.failed is True and "no_such_controller" in state.error


def test_interrupt_stops_at_the_wait_and_start_can_replace_a_running_program(rig, note):
    Note, seen = note
    programmer = Programmer(rig)
    programmer.start(Program([Wait("one", name="one"), Note("never")]))
    with pytest.raises(ProgramAlreadyRunningError):
        programmer.start(Note("x"))
    programmer.start(Note("instead"), interrupt=True)
    assert seen == ["instead"] and rig.triggers.states() == {}
    assert programmer.running is False


def test_a_timed_out_wait_ends_the_program(rig, note):
    from flyball.foundation.time import Duration

    Note, seen = note
    programmer = Programmer(rig)
    programmer.start(Program([Wait("brief", timeout=Duration(0.05)), Note("after")]))
    programmer.join(2)
    assert seen == [] and programmer.running is False
    (event,) = [e for e in rig.recent if e.level == Level.WARNING]
    assert event.kind == "step_timed_out"


def test_arrive_waits_for_a_subset_of_controllers_and_ramp_can_be_non_blocking():
    """`ramp wait: false` returns at once; `arrive` fires only when the named controllers settle."""
    import time

    from flyball.control import P
    from flyball.foundation.time import Duration
    from flyball.rig import Rig
    from flyball.sequencing import Program, Programmer
    from flyball.sequencing.loops import Arrive, Ramp, Regulate

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
            Regulate(setpoint=50.0, loop=[ca.name, cb.name]),
            Ramp(to=60.0, pace=Duration(0.01), loop=[ca.name], wait=False),
            Arrive(loop=[ca.name], within=0.5, readings=2),
        ],
        name="arrive-test",
    )
    programmer.start(program)
    assert programmer.running and programmer.state.command == "arrive"  # the ramp did not block
    deliver(b, 50.0)  # hb settling is irrelevant to an arrive on ha's controller
    deliver(b, 50.0)
    assert programmer.running
    time.sleep(0.02)  # the ramp reaches 60 in 10 ms of rig time
    deliver(a, 60.2)
    assert programmer.running  # one reading within band: not yet
    deliver(a, 59.8)
    programmer.join(2)
    assert programmer.running is False


def test_a_hold_is_a_signal_but_not_a_prompt(rig, note):
    from flyball.foundation.time import Duration
    from flyball.sequencing.loops import Hold

    Note, seen = note
    programmer = Programmer(rig)
    programmer.start(Program([Hold(Duration(60)), Note("after")]))
    (state,) = rig.triggers.states().values()
    assert state.prompt is False, "a hold settles on its own; nobody should be asked"
    programmer.start(Note("instead"), interrupt=True)


def test_a_hold_can_time_out_like_a_wait(rig, note):
    from flyball.foundation.time import Duration
    from flyball.sequencing.loops import Hold

    Note, seen = note
    programmer = Programmer(rig)
    programmer.start(Program([Hold(Duration(60), timeout=0.05), Note("after")]))
    programmer.join(2)
    assert seen == [] and programmer.running is False
    (event,) = [e for e in rig.recent if e.level == Level.WARNING]
    assert event.kind == "step_timed_out"


def test_regulate_names_a_controller_by_its_target_address(rig, fresh):
    from flyball.control import P
    from flyball.sequencing.loops import Regulate

    heater = Heater(fresh("heater"))
    rig.add_device(heater)
    controller = rig.attach_controller(
        heater.signals["power"], heater.signals["zone"], law=P(kp=2.0)
    )
    Regulate(setpoint=42.0, loop=heater.signals["power"].address).run(rig)
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
    from flyball.sequencing.loops import Arrive, Manual, Ramp, Regulate

    heater = Heater(fresh("heater"))
    rig.add_device(heater)

    for named_none in (Regulate(setpoint=1.0), Manual(), Arrive(), Ramp(to=1.0, pace=Duration(1))):
        assert named_none.missing(rig) == ["the rig has no default controller"]

    # the first controller attached becomes the default (`Controllers.add`)
    controller = rig.attach_controller(
        heater.signals["power"], heater.signals["zone"], law=P(kp=1.0)
    )

    for named_unknown in (
        Regulate(setpoint=1.0, loop="no_such"),
        Manual(loop="no_such"),
        Arrive(loop="no_such"),
        Ramp(to=1.0, pace=Duration(1), loop="no_such"),
    ):
        assert named_unknown.missing(rig) == ["controller 'no_such' is not on the rig"]

    assert Regulate(setpoint=1.0, loop=controller.name).missing(rig) == []
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
    rig.tunings.add(Tuning(tag="brisk", config=P(kp=4.0).config))

    assert Regulate(setpoint=1.0, tuning="brisk").missing(rig) == []
    assert Regulate(setpoint=1.0, tuning="ghost").missing(rig) == ["tuning 'ghost' is not stored"]
    assert Regulate(setpoint=1.0, loop="no_such", tuning="ghost").missing(rig) == [
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
