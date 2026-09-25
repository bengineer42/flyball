"""Races the rig's lock closes: a write is one step (L1, F3, F5); a long command's device (L2).

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
from flyball.foundation.device import (
    Code,
    Committable,
    Demand,
    Input,
    Permissive,
    Sample,
    command,
)
from flyball.foundation.errors import ConflictError
from flyball.model.controller import ControllerMode
from flyball.rig import Rig
from flyball.rig import stopping as stopping_mod
from flyball.rig.stopping import RigStopper
from test_server import TEMP, Daq, Drive, deliver
from test_wf2_lock import _in_thread
from test_wf3_stop import AGENT, BEN, POWER, Oven


@pytest.fixture
def oven(rig: Rig, fresh: Any) -> Oven:
    device = Oven(fresh("oven"))
    rig.add_device(device)
    now = rig.clock.now_ns()
    rig.on_samples([Sample(device.root, now, {device.signals["zone1"]: 20.0})])
    return device


def _during_the_checks(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, act: Any
) -> list[tuple[threading.Thread, list[object]]]:
    """Start `act` on another thread the first time a write checks a permissive.

    That check is part-way through the write's checks, under the rig's lock: `act` must
    wait for the whole write, so it is asserted still waiting 0.3 s later. The started
    thread is returned, for the test to join.
    """
    real = rig._not_permitted
    started: list[tuple[threading.Thread, list[object]]] = []

    def checking(signal: Any, value: Any) -> Any:
        if not started:
            started.append(_in_thread(act))
            started[0][0].join(0.3)
            assert started[0][0].is_alive(), "it waits for the write, checks and commit"
        return real(signal, value)

    monkeypatch.setattr(rig, "_not_permitted", checking)
    return started


class TestAWriteIsOneStep:
    """Its checks, apply and commit are one step under the lock (F3); nothing lands between.

    Before, the checks ran off the lock and only the latches were checked again under it:
    a stop, a `regulate` or a delivery moving a permissive's source could land in between.
    """

    def test_a_stop_during_the_checks_lands_after_the_write(self, rig, oven, monkeypatch):
        monkeypatch.setattr(stopping_mod, "LATCH_WAIT_S", 5.0)  # it waits for the lock
        during = _during_the_checks(rig, monkeypatch, lambda: RigStopper(rig).stop(BEN, "x"))
        rig.write(oven.root, {"h1": 50.0}, actor=AGENT)
        thread, _ = during[0]
        thread.join(5.0)
        h1 = [value for name, value in oven.writes if name == "h1"]
        assert h1[-2:] == [50.0, 0.0], "the write, then the stop"
        with pytest.raises(ConflictError, match="stopped"):
            rig.write(oven.root, {"h1": 50.0}, actor=AGENT)

    def test_a_regulate_during_a_persons_checks_lands_after(self, fresh, monkeypatch):
        rig = Rig()
        daq, drive = Daq(fresh("furnace")), Drive(fresh("heaters"))
        rig.add_device(daq)
        rig.add_device(drive)
        deliver(rig, daq)
        heater = rig.attach_controller(drive.signals["heater1"], daq.signals["zone1"], law=P(10))
        written: list[float] = []
        real = drive.write_signal

        def logged(signal: Any, value: float) -> None:
            written.append(value)
            real(signal, value)

        monkeypatch.setattr(drive, "write_signal", logged)
        during = _during_the_checks(rig, monkeypatch, lambda: rig.regulate(heater.name, 60.0))
        rig.write(drive.root, {"heater1": 999.0}, actor=BEN)
        thread, out = during[0]
        thread.join(2.0)
        assert out == [[heater]] and heater.mode is ControllerMode.REGULATING
        assert written[0] == 999.0 and len(written) >= 2, "the person's write, then the law's"
        rig.close()

    def test_a_permissive_moving_during_the_checks_lands_after(self, rig, oven, monkeypatch):
        rig.permit(oven.signals["h1"], Permissive(signal=f"{oven.name}.zone1", below=50.0))
        hot = Sample(oven.root, rig.clock.now_ns(), {oven.signals["zone1"]: 80.0})
        during = _during_the_checks(rig, monkeypatch, lambda: rig.on_samples([hot]))
        rig.write(oven.root, {"h1": 40.0}, actor=BEN)  # permitted when it was checked
        during[0][0].join(2.0)
        assert oven.writes[-1] == ("h1", 40.0)
        with pytest.raises(ConflictError, match="not permitted"):
            rig.write(oven.root, {"h1": 40.0}, actor=BEN)

    def test_a_reset_during_the_checks_leaves_no_false_record(self, rig, oven, monkeypatch):
        """F5: a write forced through the rig stop says so only while the stop holds."""
        RigStopper(rig).stop(BEN, "test")
        during = _during_the_checks(rig, monkeypatch, lambda: rig.stopping.reset("stop", BEN))
        rig.write(oven.root, {"h1": 50.0}, actor=BEN)
        during[0][0].join(2.0)
        codes = [e.code for e in rig.recent if e.code in (Code.WRITTEN_WHILE_STOPPED, Code.RESET)]
        assert codes == [Code.WRITTEN_WHILE_STOPPED, Code.RESET], "the write held, then reset"
        rig.write(oven.root, {"h1": 60.0}, actor=BEN)
        assert len([e for e in rig.recent if e.code == Code.WRITTEN_WHILE_STOPPED]) == 1


class TestLatchesUnderTheLock:
    def test_a_latch_is_set_and_reset_under_the_rigs_lock(self, rig, oven):
        held: list[tuple[bool, bool]] = []
        rig.stopping.latches.on_change.append(
            lambda _, on: held.append((on, rig._lock._is_owned()))
        )
        RigStopper(rig).stop(BEN, "test")
        rig.stopping.reset("stop", BEN)
        assert held == [(True, True), (False, True)]

    def test_a_stuck_lock_does_not_hold_a_latch_up(self, rig, oven, monkeypatch):
        monkeypatch.setattr(stopping_mod, "LATCH_WAIT_S", 0.1)
        monkeypatch.setattr(stopping_mod, "DEVICE_STOP_S", 0.2)
        holding, release = threading.Event(), threading.Event()

        def stuck() -> None:
            with rig._lock:
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


# region A long command claims its device (L2, F2)


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


class Fed(Pump):
    """A pump whose commit reads an input (`supply`), and that counts its commits."""

    supply = Input("supply", "Supply", TEMP)

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.commits: list[bool] = []
        self.dosing = False
        self.fail_next = False

    def commit(self, time_ns: int) -> None:
        self.commits.append(self.dosing)
        if self.fail_next:
            self.fail_next = False
            raise OSError("bus down")
        super().commit(time_ns)

    @command(long=True, writes=("rate",))
    def feed(self, seconds: float) -> None:
        """Feed for `seconds`, or until stopped."""
        self.dosing = True
        self.started.set()
        try:
            self.ended_early = self.wait(seconds)
        finally:
            self.dosing = False


class TestNoCommitDuringALongCommand:
    def test_an_input_landing_mid_command_commits_after_it(self, dosing, fresh):
        rig, _, daq, _ = dosing
        fed = Fed(fresh("fed"))
        rig.add_device(fed)
        rig.bind_inputs(fed, {"supply": f"{daq.name}.zone1"})
        thread, _ = _in_thread(rig.invoke, fed, "feed", {"seconds": 30.0})
        assert fed.started.wait(2.0)
        fed.commits.clear()
        deliver(rig, daq)  # the input lands: the device is touched
        assert fed.commits == [], "no commit while the command runs"
        rig.run_command(fed, "stop")
        thread.join(2.0)
        assert fed.commits == [False], "committed once, after the command"

    def test_a_retry_due_mid_command_waits_for_it(self, rig, fresh):
        """On a stepped clock: the retry comes up during the command, and is put off."""
        fed = Fed(fresh("fed"))
        rig.add_device(fed)
        fed.fail_next = True
        with pytest.raises(OSError):
            rig.write(fed.root, {"rate": 0.4}, actor=BEN)
        gate = threading.Event()
        fed.wait = lambda seconds: gate.wait(seconds)  # type: ignore[method-assign]
        thread, _ = _in_thread(rig.invoke, fed, "feed", {"seconds": 10.0})
        assert fed.started.wait(2.0)
        fed.commits.clear()
        rig.clock.advance(6.0)  # past the first retry (5 s)
        assert True not in fed.commits, "nothing committed during the command"
        gate.set()
        thread.join(2.0)
        rig.clock.advance(20.0)  # the retry, put off, comes up again
        assert fed.commits and fed.commits[-1] is False and 0.4 in fed.writes


# endregion


# region A stop under a stuck lock, and a stop's manual (F1, F4)


def test_a_stop_under_a_stuck_lock_waits_for_it_once(dosing, monkeypatch):
    """Cancelled at once; latched and in manual after one `LATCH_WAIT_S`, not two waits."""
    monkeypatch.setattr(stopping_mod, "DEVICE_STOP_S", 0.3)
    rig, pump, daq, drive = dosing
    heater = rig.attach_controller(drive.signals["heater1"], daq.signals["zone1"], law=P(1))
    rig.regulate(heater.name, 30.0)
    thread, _ = _dosing(rig, pump)
    holding, release = threading.Event(), threading.Event()

    def stuck() -> None:
        with rig._lock:
            holding.set()
            release.wait(10.0)

    threading.Thread(target=stuck, daemon=True).start()
    assert holding.wait(2.0)
    marks: dict[str, float] = {}
    began = time.monotonic()
    watching = threading.Event()

    def watch() -> None:
        watching.set()
        while time.monotonic() - began < 5.0 and len(marks) < 3:
            now = time.monotonic() - began
            if pump.cancelling.is_set():
                marks.setdefault("cancelled", now)
            if rig.stopping.latches.rig_stop is not None:
                marks.setdefault("latched", now)
            if heater.mode is ControllerMode.MANUAL:
                marks.setdefault("manual", now)
            time.sleep(0.002)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    watching.wait(1.0)
    try:
        RigStopper(rig).stop(BEN, "a delivery is stuck")
    finally:
        release.set()
    watcher.join(2.0)
    thread.join(2.0)
    wait = stopping_mod.LATCH_WAIT_S
    assert marks["cancelled"] < 0.1, marks
    assert marks["latched"] < wait + 0.15, marks
    assert marks["manual"] < wait + 0.15, marks


def test_a_stops_manual_is_one_step_under_the_lock(dosing, monkeypatch):
    """A `regulate` arriving mid-way through a planned stop's manual waits for all of it."""
    rig, _, daq, drive = dosing
    heater = rig.attach_controller(drive.signals["heater1"], daq.signals["zone1"], law=P(1))
    rig.regulate(heater.name, 30.0)
    real = type(heater).manual
    during: list[tuple[threading.Thread, list[object]]] = []

    def manual(self: Any) -> None:
        if not during:
            during.append(_in_thread(rig.regulate, heater.name, 40.0))
            during[0][0].join(0.2)
            assert during[0][0].is_alive(), "it waits for the stop's manual to finish"
        real(self)

    monkeypatch.setattr(type(heater), "manual", manual)
    RigStopper(rig).stop(BEN, "planned", latch=False)
    assert [e.subject for e in rig.recent if e.code == Code.INTERRUPTED] == [heater.name]
    thread, out = during[0]
    thread.join(2.0)
    assert out == [[heater]], "it regulated after the stop, whole"
    assert heater.mode is ControllerMode.REGULATING and heater.reference == 40.0


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
        with rig._lock:
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
