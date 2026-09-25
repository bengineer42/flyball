"""Races the rig's lock closes: a stop against a write (L1), a long command's device (L2).

And one lock order it no longer has to keep: a controller's own lock against the rig's (L4).

Each test lands the other thread's action exactly between a check and what it guards, by
running it from inside the first thread's check.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from flyball.control.laws import P
from flyball.foundation.device import Code, Committable, Demand, Sample, command
from flyball.foundation.errors import ConflictError
from flyball.model.controller import ControllerMode
from flyball.rig import Rig
from flyball.rig import stopping as stopping_mod
from flyball.rig.stopping import RigStopper
from test_server import Daq, Drive, deliver
from test_wf2_lock import _in_thread
from test_wf3_stop import AGENT, BEN, POWER, Oven


@pytest.fixture
def oven(rig: Rig, fresh: Any) -> Oven:
    device = Oven(fresh("oven"))
    rig.add_device(device)
    now = rig.clock.now_ns()
    rig.on_samples([Sample(device.root, now, {device.signals["zone1"]: 20.0})])
    return device


def _between_check_and_commit(rig: Rig, monkeypatch: pytest.MonkeyPatch, act: Any) -> None:
    """Run `act` on another thread once, the first time a write checks a permissive.

    That check comes after the write's latch check and before it takes the rig's lock.
    """
    real = rig._not_permitted
    done: list[bool] = []

    def checking(signal: Any, value: Any) -> Any:
        if not done:
            done.append(True)
            thread = threading.Thread(target=act, daemon=True)
            thread.start()
            thread.join(5.0)
            assert not thread.is_alive()
        return real(signal, value)

    monkeypatch.setattr(rig, "_not_permitted", checking)


class TestAStopAgainstAWrite:
    def test_a_stop_landing_after_the_check_wins(self, rig, oven, monkeypatch):
        _between_check_and_commit(rig, monkeypatch, lambda: RigStopper(rig).stop(BEN, "race"))
        with pytest.raises(ConflictError, match="stopped"):
            rig.write(oven.root, {"h1": 50.0}, actor=AGENT)
        assert ("h1", 50.0) not in oven.writes, "nothing reached the hardware after the stop"
        assert ("h1", 0.0) in oven.writes, "the stop wrote its value"

    def test_a_persons_write_goes_through_the_stop_and_says_so(self, rig, oven, monkeypatch):
        _between_check_and_commit(rig, monkeypatch, lambda: RigStopper(rig).stop(BEN, "race"))
        rig.write(oven.root, {"h1": 50.0}, actor=BEN)
        assert oven.writes[-1] == ("h1", 50.0), "a person's write passes the rig stop"
        said = [e for e in rig.recent if e.code == Code.WRITTEN_WHILE_STOPPED]
        assert len(said) == 1, "logged as written while stopped, as it is after a stop"

    def test_a_controllers_write_is_held(self, rig, oven, monkeypatch):
        controller = rig.attach_controller(oven.signals["h1"], oven.signals["zone1"])
        _between_check_and_commit(rig, monkeypatch, lambda: RigStopper(rig).stop(BEN, "race"))
        before = list(oven.writes)
        assert rig.write(oven.root, {"h1": 50.0}, by=controller) == {}
        assert ("h1", 50.0) not in oven.writes[len(before) :]


class TestLatchesUnderTheLock:
    def test_a_latch_is_set_and_reset_under_the_rigs_lock(self, rig, oven):
        held: list[tuple[bool, bool]] = []
        rig.stopping.latches.on_change.append(lambda _, on: held.append((on, rig.lock._is_owned())))
        RigStopper(rig).stop(BEN, "test")
        rig.stopping.reset("stop", BEN)
        assert held == [(True, True), (False, True)]

    def test_a_stuck_lock_does_not_hold_a_latch_up(self, rig, oven, monkeypatch):
        monkeypatch.setattr(stopping_mod, "LATCH_WAIT_S", 0.1)
        monkeypatch.setattr(stopping_mod, "DEVICE_STOP_S", 0.2)
        holding, release = threading.Event(), threading.Event()

        def stuck() -> None:
            with rig.lock:
                holding.set()
                release.wait(5.0)

        thread = threading.Thread(target=stuck, daemon=True)
        thread.start()
        assert holding.wait(2.0)
        try:
            report = RigStopper(rig).stop(BEN, "a delivery is stuck")
            assert rig.stopping.latches.rig_stop is not None, "latched all the same"
            assert report.devices[oven.name]["state"] == "failed"
        finally:
            release.set()
            thread.join(2.0)
        with pytest.raises(ConflictError, match="stopped"):
            rig.write(oven.root, {"h1": 50.0}, actor=AGENT)


# region A long command claims its device (L2)


class Pump(Committable):
    """A demand `rate` and a long `dose` that moves it, as `dosing_pump.dispense` does."""

    rate = Demand("rate", "Rate", POWER, limits=(0.0, 1.0), off=0.0)

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.started = threading.Event()
        self.ended_early: bool | None = None
        self.writes: list[float] = []

    def commit(self, time_ns: int) -> None:
        self.writes.extend(self.staged.values())

    @command(long=True, writes=("rate",))
    def dose(self, seconds: float) -> None:
        """Dose for `seconds`, or until stopped."""
        self.started.set()
        self.ended_early = self.wait(seconds)

    @command
    def note(self, text: str) -> None:
        """Drives nothing: allowed whatever runs."""

    @command(stops=True)
    def stop(self) -> None:
        """End a dose now."""
        self.cancel()


@pytest.fixture
def dosing(fresh: Any) -> Iterator[tuple[Rig, Pump, Daq, Drive]]:
    """A rig on the wall clock (a dose really waits), a pump, and a heater loop beside it."""
    rig = Rig()
    pump, daq, drive = Pump(fresh("pump")), Daq(fresh("furnace")), Drive(fresh("heaters"))
    for device in (pump, daq, drive):
        rig.add_device(device)
    deliver(rig, daq)
    yield rig, pump, daq, drive
    pump.cancel()
    rig.close()


def _dosing(rig: Rig, pump: Pump, seconds: float = 30.0) -> tuple[threading.Thread, list[object]]:
    thread, out = _in_thread(rig.invoke, pump, "dose", {"seconds": seconds})
    assert pump.started.wait(2.0)
    return thread, out


class TestALongCommandClaimsItsDevice:
    def test_a_write_to_it_is_refused_until_it_ends(self, dosing):
        rig, pump, _, _ = dosing
        thread, _ = _dosing(rig, pump)
        with pytest.raises(ConflictError, match="is running 'dose'"):
            rig.write(pump.root, {"rate": 0.5}, actor=BEN)
        assert 0.5 not in pump.writes
        rig.run_command(pump, "stop")
        thread.join(2.0)
        rig.write(pump.root, {"rate": 0.5}, actor=BEN)
        assert pump.writes[-1] == 0.5, "free again once it ended"

    def test_a_regulate_on_it_is_refused(self, dosing):
        rig, pump, daq, _ = dosing
        controller = rig.attach_controller(pump.signals["rate"], daq.signals["zone1"], law=P(1))
        thread, _ = _dosing(rig, pump)
        with pytest.raises(ConflictError, match="is running 'dose'"):
            rig.regulate(controller.name, 30.0)
        with pytest.raises(ConflictError, match="is running 'dose'"):
            controller.regulate(30.0)  # directly too: the rig's guard
        assert controller.mode is ControllerMode.MANUAL
        rig.run_command(pump, "stop")
        thread.join(2.0)
        rig.regulate(controller.name, 30.0)
        assert controller.mode is ControllerMode.REGULATING

    def test_a_command_that_drives_it_is_refused_one_that_does_not_runs(self, dosing):
        rig, pump, _, _ = dosing
        thread, _ = _dosing(rig, pump)
        with pytest.raises(ConflictError, match="is running 'dose'"):
            rig.run_command(pump, "set_rate", {"value": 0.2})
        rig.run_command(pump, "note", {"text": "halfway"})
        rig.run_command(pump, "stop")  # its stop command: never refused
        thread.join(2.0)
        assert not thread.is_alive() and pump.ended_early is True

    def test_it_is_refused_while_a_controller_drives_the_device(self, dosing):
        rig, pump, daq, _ = dosing
        controller = rig.attach_controller(pump.signals["rate"], daq.signals["zone1"], law=P(1))
        rig.regulate(controller.name, 30.0)
        with pytest.raises(ConflictError, match="driven by controller"):
            rig.invoke(pump, "dose", {"seconds": 30.0})
        assert not pump.started.is_set()

    def test_polling_and_another_loop_carry_on_and_a_stop_ends_it(self, dosing):
        """The long command is off the lock: a delivery steps a controller on another device."""
        rig, pump, daq, drive = dosing
        heater = rig.attach_controller(drive.signals["heater1"], daq.signals["zone1"], law=P(10))
        rig.regulate(heater.name, 60.0)
        thread, _ = _dosing(rig, pump)
        before = drive.inputs.get("heater1")
        daq.temps["zone1"] = 40.0
        began = time.monotonic()
        deliver(rig, daq)
        assert time.monotonic() - began < 0.5, "the delivery did not wait for the dose"
        assert drive.inputs["heater1"] != before, "the heater's controller stepped and wrote"
        began = time.monotonic()
        RigStopper(rig).stop(BEN, "done")
        thread.join(2.0)
        assert not thread.is_alive() and time.monotonic() - began < 1.0
        assert pump.ended_early is True, "the stop cancelled the dose"


# endregion


# region A controller has no lock of its own (L4)


def test_a_direct_regulate_and_a_reapply_do_not_deadlock(rig, fresh):
    """The controller's own lock is gone, and with it the order it made against the rig's.

    `Controller.regulate` held the controller's lock across its write, which takes the rig's;
    a re-apply, on the rig's timer, holds the rig's and took the controller's.
    """
    daq, drive = Daq(fresh("furnace")), Drive(fresh("heaters"))
    rig.add_device(daq)
    rig.add_device(drive)
    controller = rig.attach_controller(drive.signals["heater1"], daq.signals["zone1"], law=P(1))
    deliver(rig, daq)
    holding = threading.Event()

    def reapply_under_the_rig_lock() -> None:
        with rig.lock:
            holding.set()
            time.sleep(0.2)  # the direct regulate is now waiting for the rig's lock
            controller.reapply(rig.clock.now_ns())

    timer = threading.Thread(target=reapply_under_the_rig_lock, daemon=True)
    timer.start()
    assert holding.wait(2.0)
    direct = threading.Thread(target=controller.regulate, args=(30.0,), daemon=True)
    direct.start()
    timer.join(2.0)
    direct.join(2.0)
    assert not timer.is_alive() and not direct.is_alive(), "deadlocked"
    assert controller.mode is ControllerMode.REGULATING


# endregion
