"""Signal faults stage 2, the rig-lock rule: nothing waits or does I/O while holding `rig.lock`."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest
from flyball_sim import SteppedClock

from conftest import TestClient
from flyball.foundation.device import (
    Access,
    Device,
    Node,
    Readable,
    Role,
    Sample,
    SignalSpec,
    command,
)
from flyball.foundation.errors import ConflictError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import One
from flyball.foundation.time import Duration
from flyball.interfaces.server import create_app, set_rig
from flyball.rig import Rig
from flyball.rig import polling as polling_module
from flyball.sequencing import Program, Programmer, Step, Wait
from flyball.sequencing.devices import RunCommand

COUNT = Quantity("count", One)


class Doser(Device):
    """`dose(seconds)` waits the whole dose, as `dosing_pump.dispense` does; `stop` ends it."""

    TREE = (
        SignalSpec(name="dosed_s", quantity=COUNT, access=Access.RP, role=Role.READOUT, initial=0),
    )

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.started = threading.Event()
        self.stopped_early: bool | None = None

    @command(long=True)
    def dose(self, seconds: float) -> None:
        """Run for `seconds` of the rig's time, or until `stop`."""
        self.started.set()
        self.stopped_early = self.wait(seconds)
        self.signals["dosed_s"].push(seconds)

    @command
    def stop(self) -> None:
        """End a dose in progress."""
        self.cancel()


class Ticker(Readable):
    """Counts its reads: a device polled on its period."""

    TREE = (SignalSpec(name="n", quantity=COUNT, access=Access.RP, role=Role.READOUT),)

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.reads = 0

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        self.reads += 1
        yield Sample(self.root, time_ns, {self.signals["n"]: float(self.reads)})


def _in_thread(fn, *args) -> tuple[threading.Thread, list[object]]:  # noqa: ANN001
    out: list[object] = []

    def run() -> None:
        try:
            out.append(fn(*args))
        except Exception as error:  # the test reads it
            out.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, out


# region 1. Long commands off the lock


class TestLongCommands:
    def test_a_stop_during_a_long_command_takes_effect_at_once(self, fresh):
        rig = Rig()  # the wall clock: a 30 s dose would really wait 30 s
        doser = Doser(fresh("doser"))
        rig.add_device(doser)
        thread, out = _in_thread(rig.run_command, doser, "dose", {"seconds": 30.0})
        assert doser.started.wait(2.0)
        began = time.monotonic()
        rig.run_command(doser, "stop")
        thread.join(2.0)
        assert not thread.is_alive(), "the dose ended when stopped, not after 30 s"
        assert time.monotonic() - began < 1.0
        assert out == [None] and doser.stopped_early is True
        assert rig.router.value(doser.signals["dosed_s"]) == 30.0, "pushed after, under the lock"
        rig.close()

    def test_polling_and_deliveries_carry_on_during_a_long_command(self, fresh):
        rig = Rig()
        doser, ticker = Doser(fresh("doser")), Ticker(fresh("ticker"))
        ticker.poll_s = 0.01
        rig.add_device(doser)
        rig.add_device(ticker)
        rig.start_polling(ticker)
        thread, _ = _in_thread(rig.run_command, doser, "dose", {"seconds": 30.0})
        assert doser.started.wait(2.0)
        before = ticker.reads
        deadline = time.monotonic() + 2.0
        while ticker.reads < before + 5 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ticker.reads >= before + 5, "the poller delivered while the dose waited"
        n = ticker.signals["n"]
        assert rig.latest[n].value >= before + 5, "and each read was delivered"
        assert rig.lock.acquire(timeout=0.5), "the rig lock is free during the dose"
        rig.lock.release()
        rig.run_command(doser, "stop")
        thread.join(2.0)
        assert not thread.is_alive()
        rig.close()

    def test_a_second_long_command_on_the_device_is_refused(self, fresh):
        rig = Rig()
        doser = Doser(fresh("doser"))
        rig.add_device(doser)
        thread, _ = _in_thread(rig.run_command, doser, "dose", {"seconds": 30.0})
        assert doser.started.wait(2.0)
        with pytest.raises(ConflictError, match="is running 'dose'"):
            rig.run_command(doser, "dose", {"seconds": 1.0})
        rig.run_command(doser, "stop")
        thread.join(2.0)
        doser.started.clear()
        rig.run_command(doser, "dose", {"seconds": 0.01})  # free again, and not pre-cancelled
        assert doser.stopped_early is False
        rig.close()

    def test_a_long_command_is_refused_under_the_rig_lock(self, fresh):
        rig = Rig()
        doser = Doser(fresh("doser"))
        rig.add_device(doser)
        with rig.lock, pytest.raises(ConflictError, match="rig lock is held"):
            rig.run_command(doser, "dose", {"seconds": 1.0})
        assert not doser.started.is_set()

    def test_a_long_command_waits_on_the_rigs_clock(self, fresh):
        rig = Rig()
        rig.clock = SteppedClock(0)
        doser = Doser(fresh("doser"))
        rig.add_device(doser)
        began = time.monotonic()
        rig.run_command(doser, "dose", {"seconds": 600.0})
        assert time.monotonic() - began < 1.0, "a stepped clock steps past the wait"
        assert rig.clock.now_ns() == 600 * 10**9
        assert doser.stopped_early is False

    def test_a_program_step_runs_a_long_command_off_the_rig_lock(self, fresh):
        rig = Rig()
        doser = Doser(fresh("doser"))
        rig.add_device(doser)
        programmer = Programmer(rig)
        thread, _ = _in_thread(
            programmer.run,
            RunCommand(command="dose", device=doser.name, args={"seconds": 30}),
        )
        assert doser.started.wait(2.0)
        assert rig.lock.acquire(timeout=0.5), "the step does not hold the rig lock across the dose"
        rig.lock.release()
        rig.run_command(doser, "stop")
        thread.join(2.0)
        assert not thread.is_alive()

    def test_removing_the_device_ends_its_long_command(self, fresh):
        rig = Rig()
        doser = Doser(fresh("doser"))
        rig.add_device(doser)
        thread, out = _in_thread(rig.run_command, doser, "dose", {"seconds": 30.0})
        assert doser.started.wait(2.0)
        rig.remove_device(doser.name)
        thread.join(2.0)
        assert not thread.is_alive() and out == [None]
        assert doser.signals["dosed_s"] not in rig.latest, "what it pushed after went nowhere"


# endregion


# region 2. Fresh reads off the lock


class Hanging(Readable):
    """A read that blocks until `release` is set: a device gone quiet on its bus."""

    TREE = (SignalSpec(name="v", quantity=COUNT, access=Access.RP, role=Role.READOUT),)

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.reading = threading.Event()
        self.release = threading.Event()
        self.reads = 0

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        self.reads += 1
        self.reading.set()
        self.release.wait(10.0)
        yield Sample(self.root, time_ns, {self.signals["v"]: float(self.reads)})


class TestFreshReads:
    def test_a_hung_fresh_read_leaves_the_rig_lock_free(self, fresh):
        rig = Rig()
        hanging, ticker = Hanging(fresh("hanging")), Ticker(fresh("ticker"))
        rig.add_device(hanging)
        rig.add_device(ticker)
        thread, out = _in_thread(lambda: rig.read(hanging.signals["v"], fresh=True))
        assert hanging.reading.wait(2.0)
        # Another device's delivery, as its poll would make it, does not wait on the read.
        began = time.monotonic()
        rig.on_samples([Sample(ticker.root, rig.clock.now_ns(), {ticker.signals["n"]: 7.0})])
        assert time.monotonic() - began < 0.5
        assert rig.latest[ticker.signals["n"]].value == 7.0
        hanging.release.set()
        thread.join(2.0)
        assert out and out[0].value == 1.0, "the fresh read still delivers and answers"
        assert rig.latest[hanging.signals["v"]].value == 1.0

    def test_reads_of_one_device_do_not_overlap(self, fresh, monkeypatch):
        monkeypatch.setattr("flyball.rig.rig.FRESH_READ_WAIT_S", 0.1)
        rig = Rig()
        hanging = Hanging(fresh("hanging"))
        rig.add_device(hanging)
        thread, _ = _in_thread(lambda: rig.read(hanging.signals["v"], fresh=True))
        assert hanging.reading.wait(2.0)
        with pytest.raises(ConflictError, match="in flight"):
            rig.read(hanging.signals["v"], fresh=True)
        assert hanging.reads == 1, "the second read never reached the device"
        hanging.release.set()
        thread.join(2.0)

    def test_a_poll_waits_for_a_fresh_read_of_its_device(self, fresh):
        rig = Rig()
        rig.clock = SteppedClock(0)
        hanging = Hanging(fresh("hanging"))
        hanging.poll_s = 1.0
        rig.add_device(hanging)
        rig.start_polling(hanging)
        thread, _ = _in_thread(lambda: rig.read(hanging.signals["v"], fresh=True))
        assert hanging.reading.wait(2.0)
        poll, _ = _in_thread(rig.clock.advance, 1.0)
        poll.join(0.2)
        assert poll.is_alive() and hanging.reads == 1, "the poll waits its turn"
        hanging.release.set()
        thread.join(2.0)
        poll.join(2.0)
        assert hanging.reads == 2

    def test_a_fresh_read_is_refused_under_the_rig_lock(self, fresh):
        rig = Rig()
        hanging = Hanging(fresh("hanging"))
        rig.add_device(hanging)
        with rig.lock, pytest.raises(ConflictError, match="rig lock is held"):
            rig.read(hanging.signals["v"], fresh=True)
        assert hanging.reads == 0

    def test_what_a_removed_device_read_is_dropped(self, fresh):
        rig = Rig()
        hanging = Hanging(fresh("hanging"))
        rig.add_device(hanging)
        thread, out = _in_thread(lambda: rig.read(hanging.root, fresh=True))
        assert hanging.reading.wait(2.0)
        rig.remove_device(hanging.name)
        hanging.release.set()
        thread.join(2.0)
        assert hanging.signals["v"] not in rig.latest


# endregion


# region 3. No `rig.lock` on the event loop (ENG-26)


@pytest.fixture
def held_rig(fresh) -> Iterator[tuple[Rig, threading.Event]]:
    """A served rig whose lock another thread holds (a delivery stuck on a bus) until released."""
    rig = Rig()
    ticker = Ticker(fresh("ticker"))
    ticker.poll_s = 1.0
    rig.add_device(ticker)
    rig.on_samples([Sample(ticker.root, rig.clock.now_ns(), {ticker.signals["n"]: 1.0})])
    release, holding = threading.Event(), threading.Event()

    def hold() -> None:
        with rig.lock:
            holding.set()
            release.wait(10.0)

    thread = threading.Thread(target=hold, daemon=True)
    set_rig(rig)
    thread.start()
    assert holding.wait(2.0)
    yield rig, release
    release.set()
    thread.join(2.0)
    set_rig(None)


class TestNothingOnTheLoop:
    def test_health_answers_while_a_delivery_holds_the_lock(self, held_rig):
        _, release = held_rig
        with TestClient(create_app()) as client:
            began = time.monotonic()
            response = client.get("/api/health")
            assert time.monotonic() - began < 1.0, "not waiting for the rig's lock"
            release.set()  # the app's shutdown may take it
        assert response.status_code == 200
        assert response.json()["alarms"]["alarm"] == 0

    def test_a_websocket_primes_while_a_delivery_holds_the_lock(self, held_rig):
        rig, release = held_rig
        with TestClient(create_app()) as client:
            began = time.monotonic()
            with client.websocket_connect("/ws/samples") as ws:
                frame = ws.receive_json()
                assert time.monotonic() - began < 1.0, "primed without the rig's lock"
            release.set()
        assert frame["samples"][0]["values"] == {"n": 1.0}


# endregion


# region 4. The programmer's lock against the rig's; a bounded end


class Gated(Step, type="gated_for_wf2"):
    """A step that does nothing, under the rig's lock, as most steps run."""

    def __init__(self) -> None:
        self.entered = threading.Event()

    def run(self, rig: Rig, operator: object = None) -> None:
        self.entered.set()


class TestProgrammerLocks:
    def test_a_revoke_under_the_rig_lock_does_not_deadlock_a_start(self, fresh):
        rig = Rig()
        programmer = Programmer(rig)
        step = Gated()
        revoked = threading.Event()
        holding = threading.Event()
        started = threading.Event()

        def revoke_under_the_rig_lock() -> None:
            with rig.lock:
                holding.set()
                assert started.wait(2.0)
                time.sleep(0.2)  # the start is now waiting for the rig's lock in its step
                programmer.operator.revoke()  # on_revoke -> interrupt: the programmer's lock
                revoked.set()

        a = threading.Thread(target=revoke_under_the_rig_lock, daemon=True)
        a.start()
        assert holding.wait(2.0)

        def start() -> None:
            started.set()
            programmer.start(Program([step, step]))

        b = threading.Thread(target=start, daemon=True)
        b.start()
        assert revoked.wait(2.0), "the revoke took the programmer's lock: no ABBA"
        a.join(2.0)
        b.join(2.0)
        assert not a.is_alive() and not b.is_alive()
        assert not programmer.running
        ends = [e.code for e in rig.recent if e.subject_kind == "program"]
        assert ends[-1] == "interrupted", ends

    def test_cancel_ends_the_long_command_its_step_runs(self, fresh, monkeypatch):
        """A program's end cancels the dose its step is waiting in (`Device.cancel`)."""
        monkeypatch.setattr("flyball.sequencing.programmer.END_JOIN_S", 2.0)
        rig = Rig()
        doser = Doser(fresh("doser"))
        rig.add_device(doser)
        programmer = Programmer(rig)
        dose = RunCommand(command="dose", device=doser.name, args={"seconds": 30.0})
        programmer.start(Program([Wait(Duration(0.01)), dose]))
        assert doser.started.wait(2.0)
        began = time.monotonic()
        assert programmer.cancel() is True, "the worker unwound: its dose was cancelled"
        assert time.monotonic() - began < 1.0, "not after the 30 s dose"
        assert doser.stopped_early is True
        assert not [e for e in rig.recent if e.code == "step_still_running"]
        assert not programmer.running

    def test_cancel_waits_a_bounded_time_and_reports_a_step_still_running(self, fresh, monkeypatch):
        """A step stuck where `cancel` cannot reach (a command in its driver): bounded."""
        monkeypatch.setattr("flyball.sequencing.programmer.END_JOIN_S", 0.2)
        rig = Rig()
        doser = Doser(fresh("doser"))
        rig.add_device(doser)
        monkeypatch.setattr(doser, "cancel", lambda: None)  # a driver that does not listen
        programmer = Programmer(rig)
        dose = RunCommand(command="dose", device=doser.name, args={"seconds": 3.0})
        programmer.start(Program([Wait(Duration(0.01)), dose]))
        assert doser.started.wait(2.0)
        began = time.monotonic()
        assert programmer.cancel() is False
        assert time.monotonic() - began < 1.0, "the cancel returned, not after the 3 s dose"
        still = [e for e in rig.recent if e.code == "step_still_running"]
        assert len(still) == 1 and still[0].details["step"] == "run"
        doser.cancelling.set()
        programmer.join(2.0)
        assert not programmer.running


# endregion


# region 5. Shared collections changed while another thread iterates them


class TestSharedCollections:
    def test_a_stop_during_a_runs_update_does_not_put_the_run_back(self, fresh, monkeypatch):
        rig = Rig()
        rig.clock = SteppedClock(0)
        ticker = Ticker(fresh("ticker"))
        ticker.poll_s = 1.0
        rig.add_device(ticker)
        rig.start_polling(ticker)
        real = polling_module.replace
        stopper: list[threading.Thread] = []

        def replace_as_a_stop_lands(run, **changes):  # noqa: ANN001, ANN202
            # Another thread removes the device between `_update`'s check and its write.
            if not stopper:
                stopper.append(threading.Thread(target=rig.polling.stop, args=(ticker.name,)))
                stopper[0].start()
                stopper[0].join(0.2)
            return real(run, **changes)

        monkeypatch.setattr(polling_module, "replace", replace_as_a_stop_lands)
        rig.polling._update(ticker, read_s=0.1)
        stopper[0].join(2.0)
        assert ticker.name not in rig.polling.snapshot(), "no run for a device polled no more"

    def test_stop_all_while_a_device_is_added(self, fresh):
        rig = Rig()
        rig.clock = SteppedClock(0)
        first, second = Ticker(fresh("ticker")), Ticker(fresh("ticker"))
        for device in (first, second):
            device.poll_s = 1.0
            rig.add_device(device)
        rig.start_polling(first)
        loop = rig.polling.periodic[first.name]
        stop = loop.stop

        def stop_as_another_starts(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            rig.start_polling(second)  # a request adding a device, mid-shutdown
            return stop(*args, **kwargs)

        loop.stop = stop_as_another_starts  # type: ignore[method-assign]
        rig.polling.stop_all()  # before: "dictionary changed size during iteration"

    def test_close_while_a_writer_is_added(self, fresh):
        rig = Rig()

        class Writer:
            def __init__(self, then: object = None) -> None:
                self.then = then
                self.stopped = False

            def stop(self, join: bool = True) -> None:
                self.stopped = True
                if self.then is not None:  # a delivery makes a writer for another device
                    rig._writers[Doser(fresh("late"))] = self.then  # type: ignore[assignment]

        late = Writer()
        rig._writers[Doser(fresh("doser"))] = Writer(then=late)  # type: ignore[assignment]
        rig.close()  # before: "dictionary changed size during iteration"


# endregion


# region 6. Restarting a device whose read hangs (B3)


@pytest.fixture
def hung(fresh) -> Iterator[tuple[Rig, Hanging]]:
    """A device polled on the wall clock every 50 ms whose read is stuck in its driver."""
    rig = Rig()
    hanging = Hanging(fresh("hanging"))
    hanging.poll_s = 0.05
    rig.add_device(hanging)
    rig.start_polling(hanging)
    assert hanging.reading.wait(2.0)
    yield rig, hanging
    hanging.release.set()
    rig.close()


class TestRestartAHungDevice:
    def test_restart_answers_409_while_a_read_is_in_flight(self, hung):
        rig, hanging = hung
        set_rig(rig)
        try:
            with TestClient(create_app()) as client:
                began = time.monotonic()
                response = client.post(f"/api/devices/{hanging.name}/restart")
                assert time.monotonic() - began < 1.0, "not waiting on the hung read"
                hanging.release.set()
        finally:
            set_rig(None)
        assert response.status_code == 409
        assert "in flight" in response.json()["detail"]

    def test_starting_over_a_loop_stuck_in_a_read_is_bounded(self, hung, monkeypatch):
        monkeypatch.setattr(polling_module, "STOP_JOIN_S", 0.1)
        rig, hanging = hung
        began = time.monotonic()
        with pytest.raises(ConflictError, match="did not return"):
            rig.polling.start(hanging, 0.05)
        assert time.monotonic() - began < 1.0
        assert rig.polling.run(hanging.name).running is False

    def test_revive_reports_a_hung_device_rather_than_skip_it(self, hung):
        rig, hanging = hung
        time.sleep(0.15)  # the read has now been in flight for over its 50 ms period
        began = time.monotonic()
        assert rig.polling.revive(hanging.name) is False
        assert time.monotonic() - began < 0.5
        [event] = [e for e in rig.recent if e.code == "not_revived"]
        assert event.subject == hanging.name and event.details["reading_s"] > 0.05


# endregion
