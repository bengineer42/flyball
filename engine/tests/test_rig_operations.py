"""The rig's whole operations (D-098): each takes the rig's lock inside, so nothing lands between.

The loop commands (`regulate`, `follow`, `retune`, `manual`, `set_setpoint`), removing a
controller, and the device snapshot a route renders from. None of the routes that call them
takes the lock itself any more.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from conftest import TestClient
from flyball.control.laws import PI, P
from flyball.control.setpoint import LinearRampSetpoint
from flyball.foundation.device import Code, Sample
from flyball.foundation.errors import ConflictError
from flyball.foundation.time import Duration
from flyball.interfaces.server import create_app, set_rig
from flyball.model.controller import ControllerMode, FaultAction, ValueSource
from flyball.model.errors import LastReadingNotAvailableError
from flyball.model.generator import SetpointGenerator
from flyball.model.law import Transfer
from flyball.rig import Rig
from flyball.rig.latches import RIG_STOP, Latch, fault_cause, subjects
from test_server import Daq, Drive, deliver
from test_wf2_lock import Hanging, _in_thread
from test_wf3_stop import BEN


@pytest.fixture
def daq(rig, fresh) -> Daq:
    daq = Daq(fresh("furnace"))
    rig.add_device(daq)
    return daq


@pytest.fixture
def drive(rig, fresh) -> Drive:
    drive = Drive(fresh("heaters"))
    rig.add_device(drive)
    return drive


@pytest.fixture
def loops(rig, daq, drive):
    """Two controllers, each read once: heater1 on zone1, heater2 on zone2."""
    one = rig.attach_controller(drive.signals["heater1"], daq.signals["zone1"], law=P(kp=10.0))
    two = rig.attach_controller(drive.signals["heater2"], daq.signals["zone2"], law=P(kp=10.0))
    deliver(rig, daq)
    return one, two


def _latch(rig: Rig, cause: str, kind: Any, subject: str, action: str = "") -> None:
    rig.stopping.latch(
        Latch(
            cause=cause,
            subjects=subjects([(kind, subject)]),
            actor=BEN,
            at_utc_ns=time.time_ns(),
            reason="test",
            action=action,
        )
    )


# region The loop commands


def test_regulate_names_several_and_changes_none_if_one_is_refused(rig, loops):
    one, two = loops
    _latch(rig, fault_cause(two.name), "controller", two.name, FaultAction.FREEZE.value)
    with pytest.raises(ConflictError, match=two.name):
        rig.regulate([one.name, two.name], 30.0)
    assert one.mode is ControllerMode.MANUAL, "refused whole: the first did not change either"
    rig.stopping.reset(fault_cause(two.name), BEN)
    assert rig.regulate([one.name, two.name], 30.0) == [one, two]
    assert one.reference == two.reference == 30.0
    assert one.mode is two.mode is ControllerMode.REGULATING


def test_a_built_law_serves_one_controller(rig, loops):
    one, two = loops
    with pytest.raises(ValueError, match="one built law per controller"):
        rig.regulate([one.name, two.name], 30.0, law=PI(kp=1.0, ki=0.1))
    rig.regulate([one.name, two.name], 30.0, law=PI.config_type(kp=1.0, ki=0.1))
    assert one.law is not two.law and one.law.config.type == two.law.config.type == "pi"


def test_follow_starts_each_profile_by_the_rule(rig, loops, daq):
    one, two = loops
    rig.regulate(one.name, 40.0)
    starts = rig.follow([one.name, two.name], lambda: LinearRampSetpoint(Duration(100.0), end=50.0))
    # One on a setpoint starts from it; one without, from its last reading.
    assert starts == {one: 40.0, two: daq.temps["zone2"]}
    assert isinstance(one.reference, SetpointGenerator)
    assert one.reference is not two.reference, "one profile each"
    with pytest.raises(ValueError, match="one profile per controller"):
        rig.follow([one.name, two.name], LinearRampSetpoint(Duration(1.0), end=1.0))


def test_follow_with_nothing_to_start_from_changes_nothing(rig, daq, drive):
    controller = rig.attach_controller(drive.signals["heater1"], daq.signals["zone1"], law=P(kp=1))
    with pytest.raises(LastReadingNotAvailableError):
        rig.follow(controller.name, LinearRampSetpoint(Duration(10.0), end=50.0))
    assert controller.mode is ControllerMode.MANUAL and controller.reference is None


def test_retune_keeps_the_trajectory_and_the_output(rig, loops, clock, daq):
    one, _ = loops
    rig.follow(one.name, LinearRampSetpoint(Duration(100.0), end=200.0))
    clock.advance(1.0)
    deliver(rig, daq)
    reference, output = one.reference, one.output_value
    assert rig.retune(one.name, PI.config_type(kp=2.0, ki=0.5)) == [one]
    assert one.law is not None and one.law.config.type == "pi"
    assert one.reference is reference, "still following the ramp"
    assert one.mode is ControllerMode.REGULATING
    assert one.output_value == pytest.approx(output), "bumpless"


def test_retune_in_manual_only_swaps_the_law(rig, loops):
    one, _ = loops
    rig.retune(one.name, PI.config_type(kp=2.0, ki=0.5))
    assert one.law is not None and one.law.config.type == "pi"
    assert one.mode is ControllerMode.MANUAL and one.reference is None


def test_manual_and_set_setpoint(rig, loops):
    one, two = loops
    rig.regulate([one.name, two.name], 30.0)
    assert rig.manual([one.name, two.name]) == [one, two]
    assert one.mode is two.mode is ControllerMode.MANUAL
    rig.set_setpoint(one.name, 35.0)
    assert one.reference == 35.0 and one.mode is ControllerMode.MANUAL
    rig.set_setpoint(None, LinearRampSetpoint(Duration(10.0), end=50.0), start=ValueSource.SETPOINT)
    assert isinstance(one.reference, SetpointGenerator), "None: the default controller"


def test_a_persons_regulate_resets_the_controllers_own_manual_latch(rig, loops):
    one, _ = loops
    _latch(rig, fault_cause(one.name), "controller", one.name, FaultAction.MANUAL.value)
    with pytest.raises(ConflictError):
        rig.regulate(one.name, 30.0)  # not a person: the latch refuses it
    rig.regulate(one.name, 30.0, actor=BEN)
    assert rig.stopping.latches.get(fault_cause(one.name)) is None
    assert one.mode is ControllerMode.REGULATING


def test_a_persons_regulate_does_not_reset_past_another_latch(rig, loops):
    one, _ = loops
    _latch(rig, fault_cause(one.name), "controller", one.name, FaultAction.MANUAL.value)
    _latch(rig, RIG_STOP, "rig", "rig")
    with pytest.raises(ConflictError, match="stopped"):
        rig.regulate(one.name, 30.0, actor=BEN)
    assert rig.stopping.latches.get(fault_cause(one.name)) is not None, "left as it was"


def test_the_latch_reset_and_the_aim_are_one_operation(rig, loops, monkeypatch):
    """Two people's regulates at once: the second waits for the first, whole (was: a 404).

    The route used to check the latch, reset it and aim as three steps with no lock between
    them; a second regulate landing between the first's check and its reset reset the latch
    first, and the first's reset then found nothing to reset.
    """
    one, _ = loops
    cause = fault_cause(one.name)
    _latch(rig, cause, "controller", one.name, FaultAction.MANUAL.value)
    reset = rig.stopping.reset
    second: list[tuple[threading.Thread, list[object]]] = []

    def reset_as_another_arrives(cause: str, actor: Any) -> Latch:
        if not second:  # the other request lands now, between this one's check and its reset
            second.append(_in_thread(lambda: rig.regulate(one.name, 35.0, actor=BEN)))
            second[0][0].join(0.2)
            assert second[0][0].is_alive(), "it waits for the first to finish"
        return reset(cause, actor)

    monkeypatch.setattr(rig.stopping, "reset", reset_as_another_arrives)
    assert rig.regulate(one.name, 30.0, actor=BEN) == [one]
    thread, out = second[0]
    thread.join(2.0)
    assert out == [[one]], "the second regulated too, after the first"
    assert one.reference == 35.0 and one.mode is ControllerMode.REGULATING
    resets = [e for e in rig.recent if e.code == Code.RESET]
    assert len(resets) == 1, "reset once"


def test_detach_puts_the_controller_in_manual_while_still_wired(rig, loops, drive, daq, clock):
    one, _ = loops
    rig.regulate(one.name, 60.0, transfer=Transfer.COLD)
    clock.advance(1.0)
    deliver(rig, daq)
    held = drive.inputs["heater1"]
    rig.detach_controller(one.name)
    assert one.mode is ControllerMode.MANUAL
    clock.advance(1.0)
    deliver(rig, daq)
    assert drive.inputs["heater1"] == held


# endregion

# region Reads


def test_a_device_snapshot_is_a_copy_from_one_instant(rig, daq, clock):
    deliver(rig, daq)
    snapshot = rig.device_snapshot(daq)
    zone1 = daq.signals["zone1"]
    assert snapshot.latest[zone1].value == 21.5 and snapshot.kind == "device"
    daq.temps["zone1"] = 99.0
    clock.advance(1.0)
    deliver(rig, daq)
    assert snapshot.latest[zone1].value == 21.5, "what a delivery lands later is not in it"
    assert rig.device_snapshot(daq).latest[zone1].value == 99.0


@pytest.fixture
def served(rig, daq, drive, fresh) -> Iterator[tuple[Rig, Hanging]]:
    hanging = Hanging(fresh("hanging"))
    rig.add_device(hanging)
    deliver(rig, daq)
    set_rig(rig)
    yield rig, hanging
    hanging.release.set()
    set_rig(None)


def test_a_hung_fresh_read_does_not_block_another_route(served, daq, drive):
    rig, hanging = served
    with TestClient(create_app()) as client:
        thread, out = _in_thread(client.get, f"/api/read/{hanging.name}.v?fresh=true")
        assert hanging.reading.wait(2.0), "the fresh read is in the driver"
        began = time.monotonic()
        device = client.get(f"/api/devices/{daq.name}")
        written = client.put(f"/api/devices/{drive.name}/write", json={"heater1": 5.0})
        read = client.get(f"/api/read/{daq.name}.zone1")
        assert time.monotonic() - began < 1.0, "none of them waited for the hung read"
        hanging.release.set()
        thread.join(2.0)
    assert device.status_code == written.status_code == read.status_code == 200
    assert out and out[0].status_code == 200  # type: ignore[attr-defined]


def test_an_answer_comes_from_the_snapshot_not_under_the_lock(served, daq, monkeypatch):
    """`device_out` renders off the rig's lock: another thread may take it meanwhile."""
    rig, _ = served
    from flyball.interfaces.server import schemas

    real = schemas.DeviceOut.of
    held: list[bool] = []

    def rendering(snapshot: Any, *, link: Any) -> Any:
        held.append(rig.lock._is_owned())
        return real(snapshot, link=link)

    monkeypatch.setattr(schemas.DeviceOut, "of", rendering)
    with TestClient(create_app()) as client:
        assert client.get(f"/api/devices/{daq.name}").status_code == 200
    assert held == [False]
    rig.on_samples([Sample(daq.root, rig.clock.now_ns(), {daq.signals["zone1"]: 1.0})])


# endregion
