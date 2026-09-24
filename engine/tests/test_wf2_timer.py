"""Signal faults stage 5: the rig-clock timer, and what runs on it.

`Timers` (one-shot and periodic calls on any clock); liveness (`stale(silent |
never_read | last_read)` pushed at the threshold, `hung`); A4's `invalid` grace
and A5's fault time judged on the timer; A6's write retry; E25's re-apply of a
moving setpoint's feedforward between readings.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator

import pytest
from flyball_sim import ScaledClock, SteppedClock

from conftest import TestClient
from flyball.control.laws import PI, P
from flyball.control.setpoint import LinearRampSetpoint
from flyball.foundation import Clock, Speed
from flyball.foundation.device import (
    Access,
    Code,
    Committable,
    DeviceEntry,
    Node,
    NoValue,
    Quality,
    Readable,
    Role,
    Sample,
    Signal,
    SignalSpec,
    invalid,
    not_applicable,
)
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Watt
from flyball.foundation.time import Timers, TimeUnit
from flyball.interfaces.server import create_app, set_rig
from flyball.model.controller import ControllerMode, FaultAction, OnFault
from flyball.record import Flag
from flyball.record.sqlite import SqliteStore
from flyball.rig import Rig
from flyball.rig.faults import Outage
from flyball.runtime.config import ControllerEntry

TEMP = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)
S = 1_000_000_000


def until(condition: Callable[[], bool], timeout_s: float = 5.0) -> None:
    """Wait, in real time, for something a thread does; fail after `timeout_s`."""
    deadline = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert condition()


# region The timer


class TestTimersOnASteppedClock:
    def test_a_one_shot_runs_once_at_its_time(self):
        clock = SteppedClock(0)
        timers = Timers(clock)
        at: list[int] = []
        timers.after(2.5, lambda: at.append(clock.monotonic_ns()))
        clock.advance(2.0)
        assert at == []
        clock.advance(10.0)
        assert at == [int(2.5 * S)]
        assert timers.pending == 0

    def test_periodic_calls_keep_their_period_in_time_order_with_one_shots(self):
        clock = SteppedClock(0)
        timers = Timers(clock)
        seen: list[tuple[str, float]] = []
        timers.every(1.0, lambda: seen.append(("tick", clock.monotonic())))
        timers.after(1.5, lambda: seen.append(("once", clock.monotonic())))
        clock.advance(3.0)
        assert seen == [("tick", 1.0), ("once", 1.5), ("tick", 2.0), ("tick", 3.0)]

    def test_a_cancelled_call_does_not_run_and_a_call_may_cancel_and_add(self):
        clock = SteppedClock(0)
        timers = Timers(clock)
        ran: list[str] = []
        late = timers.after(2.0, lambda: ran.append("late"))

        def first() -> None:
            ran.append("first")
            late.cancel()
            timers.after(0.5, lambda: ran.append("added"))

        timers.after(1.0, first)
        clock.advance(5.0)
        assert ran == ["first", "added"]

    def test_a_call_that_raises_is_counted_and_the_period_goes_on(self):
        clock = SteppedClock(0)
        timers = Timers(clock)
        calls: list[float] = []

        def boom() -> None:
            calls.append(clock.monotonic())
            raise RuntimeError("boom")

        timers.every(1.0, boom)
        clock.advance(3.0)
        assert calls == [1.0, 2.0, 3.0] and timers.errors == 3
        assert isinstance(timers.last_error, RuntimeError)

    def test_close_cancels_everything_and_refuses_more(self):
        clock = SteppedClock(0)
        timers = Timers(clock)
        ran: list[int] = []
        timers.every(1.0, lambda: ran.append(1))
        assert timers.close() is True
        clock.advance(5.0)
        assert ran == [] and clock.scheduled == 0
        with pytest.raises(RuntimeError, match="closed"):
            timers.after(1.0, lambda: None)

    def test_another_thread_may_add_while_the_clock_runs_a_call(self):
        """The clock's schedule lock is not held while a call runs.

        So no deadlock with a thread that holds what the call waits for (the rig's lock)
        and adds a timer meanwhile.
        """
        clock = SteppedClock(0)
        timers = Timers(clock)
        lock = threading.Lock()
        entered, added = threading.Event(), threading.Event()

        def holder() -> None:
            with lock:
                entered.set()
                clock.call_later(1.0, lambda: None)  # on the clock while it advances
                added.set()

        def call() -> None:
            thread = threading.Thread(target=holder)
            thread.start()
            entered.wait(1.0)
            with lock:  # what a timer call does: take the rig's lock
                pass
            thread.join(1.0)

        timers.after(1.0, call)
        clock.advance(1.0)
        assert added.is_set()

    def test_a_clock_with_only_a_periodic_schedule_runs_one_shots_once(self):
        """An older `flyball-sim` has no `call_later`: its `schedule` is used, then cancelled."""

        class Older(SteppedClock):
            call_later = None  # type: ignore[assignment]

        clock = Older(0)
        timers = Timers(clock)
        at: list[float] = []
        timers.after(1.0, lambda: at.append(clock.monotonic()))
        timers.every(2.0, lambda: at.append(-clock.monotonic()))
        clock.advance(4.0)
        assert at == [1.0, -2.0, -4.0]


class TestTimersOnAThread:
    def test_a_one_shot_on_the_wall_clock(self):
        timers = Timers(Clock(), "test")
        done = threading.Event()
        timers.after(0.02, done.set)
        assert done.wait(2.0)
        assert timers.close() is True

    def test_an_earlier_call_wakes_the_thread(self):
        timers = Timers(Clock(), "test")
        order: list[str] = []
        timers.after(30.0, lambda: order.append("late"))
        early = threading.Event()
        timers.after(0.01, lambda: (order.append("early"), early.set()))
        assert early.wait(2.0) and order == ["early"]
        timers.close()

    def test_a_scaled_clock_fires_at_the_scaled_time(self):
        clock = ScaledClock(speed=100.0)
        timers = Timers(clock, "test")
        fired = threading.Event()
        started = time.monotonic()
        timers.after(2.0, fired.set)  # 2 s of the clock's time: 20 ms of real time
        assert fired.wait(2.0)
        assert time.monotonic() - started < 1.0
        timers.close()

    def test_close_waits_a_bounded_time_for_a_stuck_call(self):
        timers = Timers(Clock(), "test")
        release, inside = threading.Event(), threading.Event()

        def stuck() -> None:
            inside.set()
            release.wait(5.0)

        timers.after(0.0, stuck)
        assert inside.wait(2.0)
        started = time.monotonic()
        assert timers.close(timeout=0.1) is False, "abandoned, not waited on"
        assert time.monotonic() - started < 1.0
        release.set()

    def test_close_stops_an_idle_thread_promptly(self):
        timers = Timers(Clock(), "test")
        timers.after(60.0, lambda: None)
        started = time.monotonic()
        assert timers.close() is True
        assert time.monotonic() - started < 0.5


class TestTheRigsTimers:
    def test_they_run_on_the_rig_clock_and_close_with_the_rig(self, rig, clock):
        ran: list[float] = []
        rig.after(1.0, lambda: ran.append(clock.monotonic()))
        clock.advance(2.0)
        assert ran == [1.0]
        rig.after(1.0, lambda: ran.append(-1))
        rig.close()
        clock.advance(2.0)
        assert ran == [1.0]
        assert rig.after(1.0, lambda: None) is None, "nothing is armed on a closed rig"

    def test_swapping_the_clock_rebuilds_them(self):
        rig = Rig()
        first = rig.timers
        rig.clock = SteppedClock(0)
        assert rig.timers is not first and first.closed
        rig.close()


# endregion

# region Liveness


class Probe(Readable, Committable):
    """Two readouts and an echo demand; `mute` yields nothing, `only_a` only `a`."""

    TREE = (
        SignalSpec(name="a", quantity=TEMP, access=Access.RP),
        SignalSpec(name="b", quantity=TEMP, access=Access.RP),
        SignalSpec(name="out", quantity=TEMP, role=Role.DEMAND, access=Access.RPW),
        SignalSpec(name="mode", quantity=TEMP, role=Role.SETTING, access=Access.RP),
    )

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.mute = False
        self.only_a = False
        self.a: object = 20.0
        self.fail_reads = False
        self.fail_commits = False
        self.commits_at: list[float] = []
        self.clock: SteppedClock | None = None

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        if self.fail_reads:
            raise OSError("bus timeout")
        if self.mute:
            return
        values: dict[Signal, object] = {self.signals["a"]: self.a}
        if not self.only_a:
            values[self.signals["b"]] = 1.0
        yield Sample(self.root, time_ns, values)

    def commit(self, time_ns: int) -> None:
        if self.clock is not None:
            self.commits_at.append(self.clock.monotonic())
        if self.fail_commits:
            raise OSError("bus timeout")
        super().commit(time_ns)


@pytest.fixture
def probe(rig: Rig, clock: SteppedClock, fresh) -> Probe:
    probe = Probe(fresh("probe"))
    probe.poll_s = 1.0
    probe.clock = clock
    rig.add_device(probe)
    return probe


def _latest(rig: Rig, signal: Signal):
    return rig.router.latest[signal]


class TestLiveness:
    def test_a_silent_signal_goes_stale_at_its_threshold_and_breaks_there(self, rig, clock, probe):
        rig.start_polling(probe)
        clock.advance(3.0)  # read at 1, 2, 3
        probe.mute = True
        clock.advance(4.9)
        assert _latest(rig, probe.signals["a"]).value == 20.0, "not yet: 3 + 5 s"
        clock.advance(0.1)
        reading = _latest(rig, probe.signals["a"])
        assert reading.value == NoValue(Quality.STALE, "silent")
        assert reading.time_ns == 8 * S, "stamped at the threshold: the chart breaks there"
        assert _latest(rig, probe.signals["b"]).value == NoValue(Quality.STALE, "silent")

    def test_one_signal_the_device_stops_sending_is_last_read(self, rig, clock, probe):
        rig.start_polling(probe)
        clock.advance(3.0)
        probe.only_a = True
        clock.advance(5.0)
        assert _latest(rig, probe.signals["b"]).value == NoValue(Quality.STALE, "last_read")
        assert _latest(rig, probe.signals["a"]).value == 20.0

    def test_a_signal_never_read_leaves_pending_at_its_deadline(self, rig, clock, probe):
        probe.only_a = True
        rig.start_polling(probe)
        clock.advance(5.9)
        assert probe.signals["b"] not in rig.router.latest, "pending until the first read + 5 s"
        clock.advance(0.1)
        assert _latest(rig, probe.signals["b"]).value == NoValue(Quality.STALE, "never_read")

    def test_a_device_that_never_reads_counts_from_bind_with_its_own_reason(
        self, rig, clock, probe
    ):
        probe.fail_reads = True
        rig.start_polling(probe)
        clock.advance(4.9)
        assert probe.signals["a"] not in rig.router.latest, "pending: offline, never read"
        clock.advance(0.1)  # polled from 0: never read by 5 s, and its device is offline
        assert _latest(rig, probe.signals["a"]).value == NoValue(Quality.STALE, "device_offline")

    def test_an_arrival_ends_it(self, rig, clock, probe):
        rig.start_polling(probe)
        clock.advance(1.0)
        probe.mute = True
        clock.advance(6.0)
        assert not _latest(rig, probe.signals["a"]).usable
        probe.mute = False
        clock.advance(1.0)
        assert _latest(rig, probe.signals["a"]).value == 20.0
        probe.mute = True
        clock.advance(5.0)
        assert _latest(rig, probe.signals["a"]).value == NoValue(Quality.STALE, "silent")

    def test_not_applicable_is_not_judged(self, rig, clock, probe):
        rig.start_polling(probe)
        clock.advance(1.0)
        probe.a = not_applicable("no flow")
        clock.advance(1.0)
        probe.mute = True
        clock.advance(20.0)
        assert _latest(rig, probe.signals["a"]).value == not_applicable("no flow")
        assert _latest(rig, probe.signals["b"]).value == NoValue(Quality.STALE, "silent")

    def test_echo_demands_and_settings_are_not_judged(self, rig, clock, probe):
        rig.start_polling(probe)
        rig.write(probe.root, {"out": 1.0})
        probe.mute = True
        clock.advance(30.0)
        assert _latest(rig, probe.signals["out"]).value == 1.0
        assert rig.liveness.threshold_s(probe.signals["out"]) is None
        assert rig.liveness.threshold_s(probe.signals["mode"]) is None
        assert rig.liveness.threshold_s(probe.signals["a"]) == 5.0

    def test_the_threshold_is_three_periods_or_its_own(self, rig, clock, fresh):
        slow = Probe(fresh("slow"))
        slow.poll_s = 4.0
        slow.signals["b"].set_meta(stale_after_s=2.5)
        rig.add_device(slow)
        rig.start_polling(slow)
        assert rig.liveness.threshold_s(slow.signals["a"]) == 12.0
        assert rig.liveness.threshold_s(slow.signals["b"]) == 2.5

    def test_a_push_signal_is_judged_only_with_its_own_stale_after(self, rig, clock, fresh):
        pushed = Probe(fresh("pushed"))
        pushed.signals["a"].set_meta(stale_after_s=2.0)
        rig.add_device(pushed)  # never polled
        assert rig.liveness.threshold_s(pushed.signals["b"]) is None
        pushed.signals["a"].push(20.0)
        clock.advance(2.0)
        assert _latest(rig, pushed.signals["a"]).value == NoValue(Quality.STALE, "silent")

    def test_the_reading_carries_when_it_was_received(self, rig, clock, probe):
        rig.start_polling(probe)
        clock.advance(1.0)
        reading = _latest(rig, probe.signals["a"])
        assert reading.received_ns == 1 * S and reading.time_ns == 1 * S

    def test_a_controller_on_it_freezes(self, rig, clock, probe):
        controller = rig.attach_controller(probe.signals["out"], probe.signals["a"], law=P(kp=1.0))
        rig.start_polling(probe)
        clock.advance(1.0)
        controller.regulate(25.0)
        probe.mute = True
        clock.advance(6.0)
        frozen = rig.conditions.get(controller, Code.FROZEN)
        assert frozen is not None and frozen.details["reason"] == "silent"

    def test_the_wire_says_the_threshold(self, rig, clock, probe):
        rig.start_polling(probe)
        set_rig(rig)
        try:
            with TestClient(create_app()) as client:
                device = client.get(f"/api/devices/{probe.name}").json()
        finally:
            set_rig(None)
        by_name = {s["name"]: s for s in device["signals"]}
        assert by_name["a"]["stale_after_s"] == 5.0
        assert by_name["out"]["stale_after_s"] is None

    def test_the_stale_reading_is_stored_as_a_break(self, rig, clock, probe, tmp_path):
        store = SqliteStore(tmp_path / "s.db")
        rig.start_polling(probe)
        session = rig.start_recording(store).writer.session.id
        clock.advance(1.0)
        probe.mute = True
        clock.advance(6.0)
        rig.stop_recording()
        points = store.series(session, probe.signals["a"].address).points
        assert (points[0].value, points[-1].value) == (20.0, None)
        assert points[-1].flag == Flag.STALE and points[-1].offset_ns == 6 * S
        store.close()


class TestHung:
    def test_a_read_stuck_past_its_bound_is_hung_and_its_read_path_stale(self, fresh):
        """On a scaled clock (100x): the bound is 5 s of rig time, 50 ms of real time."""
        rig = Rig()
        rig.clock = ScaledClock(speed=100.0)
        stuck, release = threading.Event(), threading.Event()

        class Sticky(Probe):
            def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
                if stuck.is_set():
                    release.wait(10.0)
                yield from super().read(time_ns, node)

        sticky = Sticky(fresh("sticky"))
        sticky.poll_s = 0.5
        rig.add_device(sticky)
        try:
            rig.start_polling(sticky)
            until(lambda: sticky.signals["a"] in rig.router.latest)
            stuck.set()
            until(lambda: rig.conditions.get(sticky, Code.HUNG) is not None)
            gone = NoValue(Quality.STALE, "device_hung")
            until(lambda: rig.router.latest[sticky.signals["a"]].value == gone)
            held = rig.conditions.get(sticky, Code.HUNG)
            assert held is not None and held.details["bound_s"] == 5.0
            stuck.clear()
            release.set()
            until(lambda: rig.conditions.get(sticky, Code.HUNG) is None)
            until(lambda: rig.router.latest[sticky.signals["a"]].value == 20.0)
        finally:
            release.set()
            rig.close()


# endregion

# region A4: the invalid grace on the timer


class TestInvalidGrace:
    def test_band_unknown_is_raised_when_the_grace_is_up_with_nothing_delivered(
        self, rig, clock, probe
    ):
        a = probe.signals["a"]
        a.set_meta(alarm=(0.0, 100.0))
        rig.start_polling(probe)
        clock.advance(1.0)
        probe.a = invalid("crc")
        clock.advance(1.0)  # one invalid at 2 s, then nothing
        probe.mute = True
        clock.advance(1.9)
        assert rig.conditions.get(a, Code.BAND_UNKNOWN) is None
        clock.advance(0.1)  # 2 s of fault time: max(2·poll_s, 1 s)
        held = rig.conditions.get(a, Code.BAND_UNKNOWN)
        assert held is not None and held.since_ns == 4 * S

    def test_a_blip_raises_nothing(self, rig, clock, probe):
        a = probe.signals["a"]
        a.set_meta(alarm=(0.0, 100.0))
        rig.start_polling(probe)
        clock.advance(1.0)
        probe.a = invalid("crc")
        clock.advance(1.0)
        probe.a = 20.0
        clock.advance(10.0)
        assert rig.conditions.get(a, Code.BAND_UNKNOWN) is None


# endregion

# region A5: fault time on the timer


@pytest.fixture
def loop(rig: Rig, clock: SteppedClock, probe: Probe):
    """A regulating P controller on `probe.a` through `probe.out`, polled every second."""
    released: list[Outage] = []
    rig.faults.on_fault.append(released.append)
    controller = rig.attach_controller(
        probe.signals["out"],
        probe.signals["a"],
        law=P(kp=1.0),
        on_fault=OnFault(FaultAction.MANUAL),  # plain freeze is never released
    )
    rig.start_polling(probe)
    clock.advance(1.0)
    controller.regulate(25.0)
    return controller, released


class TestFaultTime:
    def test_an_offline_source_is_released_on_the_timer_with_no_delivery(
        self, rig, clock, probe, loop
    ):
        controller, released = loop
        probe.fail_reads = True
        clock.advance(3.0)  # reads at 2, 3, 4 raise: offline at 4 s
        assert rig.conditions.get(probe, Code.OFFLINE) is not None
        assert released == []
        clock.advance(1.9)
        assert released == []
        clock.advance(0.1)  # max(2·poll_s, 1 s) = 2 s after the offline edge
        (outage,) = released
        assert outage.controller is controller and outage.released_ns == 6 * S
        assert outage.reason == "stale(device_offline)"

    def test_a_silent_source_is_released_when_it_is_declared_stale(self, rig, clock, probe, loop):
        _, released = loop
        probe.mute = True
        clock.advance(5.0)  # last arrival at 1 s: stale(silent) at 6 s, no added wait
        (outage,) = released
        assert outage.released_ns == 6 * S and outage.reason == "stale(silent)"

    def test_a_blip_is_not_released_and_three_values_end_the_outage(self, rig, clock, probe, loop):
        controller, released = loop
        probe.a = invalid("crc")
        clock.advance(1.0)
        probe.a = 20.0
        clock.advance(1.0)
        assert controller in rig.faults.outages and rig.faults.accrued_s(controller) == 1.0
        clock.advance(2.0)
        assert controller not in rig.faults.outages, "3 values in a row: over, the time reset"
        clock.advance(10.0)
        assert released == []

    def test_a_flapping_source_accrues_and_is_released(self, rig, clock, probe, loop):
        controller, released = loop
        for _ in range(3):
            probe.a = invalid("crc")
            clock.advance(1.0)
            probe.a = 20.0
            clock.advance(1.0)
            if released:
                break
        assert len(released) == 1, "1 s per blip, never 3 fresh in a row: 2 s accrued"

    def test_a_manual_controller_is_not_released_until_it_regulates(self, rig, clock, probe, loop):
        controller, released = loop
        controller.manual()
        probe.a = invalid("crc")
        clock.advance(5.0)
        assert released == []
        with rig.lock:
            controller.regulate(25.0)
        (outage,) = released
        assert outage.controller is controller

    def test_a_law_error_is_released_at_once(self, rig, clock, probe, loop):
        controller, released = loop
        controller.law = None  # stepping it raises
        clock.advance(1.0)
        (outage,) = released
        assert outage.reason.startswith("law_error")


# endregion

# region A6: write retry


class TestWriteRetry:
    def test_a_failed_write_is_retried_on_the_clock_without_other_traffic(self, rig, clock, probe):
        probe.fail_commits = True
        with pytest.raises(OSError):
            rig.write(probe.root, {"out": 42.0})
        assert rig.conditions.get(probe, Code.WRITE_FAILED) is not None
        probe.fail_commits = False
        clock.advance(0.9)
        assert probe.commits_at == [0.0], "the first retry comes at min(poll_s, 5 s)"
        clock.advance(0.1)
        assert probe.commits_at == [0.0, 1.0]
        assert _latest(rig, probe.signals["out"]).value == 42.0
        assert rig.conditions.get(probe, Code.WRITE_FAILED) is None
        (resent,) = [e for e in rig.recent if e.code == Code.RESENT]
        assert "re-sent" in resent.message and resent.details["value"] == 42.0

    def test_retries_back_off_and_a_value_too_old_is_dropped(self, rig, clock, fresh):
        quiet = Probe(fresh("quiet"))  # no poll_s: the first retry at 5 s
        quiet.clock = clock
        rig.add_device(quiet)
        rig.write(quiet.root, {"out": 1.0})
        quiet.fail_commits = True
        with pytest.raises(OSError):
            rig.write(quiet.root, {"out": 42.0})
        clock.advance(200.0)
        assert quiet.commits_at == [0.0, 0.0, 5.0, 15.0, 35.0], "5, doubling; dropped at 75"
        (dropped,) = [e for e in rig.recent if e.code == Code.WRITE_DROPPED]
        assert dropped.details["value"] == 42.0 and dropped.details["age_s"] == 75.0
        assert _latest(rig, quiet.signals["out"]).value == NoValue(Quality.STALE, "write_failed")
        assert rig.conditions.get(quiet, Code.WRITE_FAILED) is not None
        quiet.fail_commits = False
        rig.write(quiet.root, {"out": 7.0})
        assert _latest(rig, quiet.signals["out"]).value == 7.0, "a new demand commits it"

    def test_retry_max_age_s_is_the_device_s_key(self, rig, clock, fresh):
        entry = DeviceEntry(driver="whatever", retry_max_age_s=10.0)
        assert entry.retry_max_age_s == 10.0
        with pytest.raises(ValueError, match="retry_max_age_s"):
            DeviceEntry(driver="whatever", retry_max_age_s=0.0)
        quiet = Probe(fresh("quiet"))
        quiet.clock = clock
        rig.add_device(quiet)
        rig.entries[quiet.name] = entry
        quiet.fail_commits = True
        with pytest.raises(OSError):
            rig.write(quiet.root, {"out": 42.0})
        clock.advance(100.0)
        assert quiet.commits_at == [0.0, 5.0], "dropped at the 15 s retry: older than 10 s"

    def test_the_writer_retries_too(self, rig, clock, fresh):
        class Blocking(Probe):
            blocking = True

        oven = Blocking(fresh("blocking"))
        rig.add_device(oven)
        out = oven.signals["out"]
        oven.fail_commits = True
        rig.write(oven.root, {"out": 42.0})
        until(lambda: rig.conditions.get(oven, Code.WRITE_FAILED) is not None)
        oven.fail_commits = False
        clock.advance(5.0)  # the retry: the writer commits what it kept
        until(lambda: rig.router.latest.get(out) is not None and _latest(rig, out).value == 42.0)
        until(lambda: any(e.code == Code.RESENT for e in list(rig.recent)))
        assert rig.conditions.get(oven, Code.WRITE_FAILED) is None


# endregion

# region E25: re-apply between readings


class Heater(Readable, Committable):
    """A zone read every 2 s and a target in the same unit: identity feedforward."""

    TREE = (
        SignalSpec(name="zone", quantity=TEMP, access=Access.RP),
        SignalSpec(name="target", quantity=TEMP, role=Role.DEMAND, access=Access.RPW),
    )

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.zone: object = 20.0
        self.targets: list[tuple[float, float]] = []
        self.clock: SteppedClock | None = None

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        yield Sample(self.root, time_ns, {self.signals["zone"]: self.zone})

    def write_signal(self, signal: Signal, value: float) -> None:
        assert self.clock is not None
        self.targets.append((self.clock.monotonic(), value))


@pytest.fixture
def heater(rig: Rig, clock: SteppedClock, fresh) -> Heater:
    heater = Heater(fresh("heater"))
    heater.poll_s = 2.0
    heater.clock = clock
    rig.add_device(heater)
    rig.start_polling(heater)
    return heater


def _ramp(controller, end: float = 30.0, per_second: float = 1.0) -> None:
    controller.regulate(20.0, LinearRampSetpoint(Speed(per_second, TimeUnit.SECOND), end))


class TestReapply:
    def test_a_ramp_moves_the_target_between_readings(self, rig, clock, heater):
        controller = rig.attach_controller(
            heater.signals["target"], heater.signals["zone"], law=P(kp=0.0)
        )
        clock.advance(2.0)
        _ramp(controller)
        assert rig.setpoint_period_s(controller) == 0.5, "max(0.1 s, poll_s / 4)"
        clock.advance(2.0)
        times = [t for t, _ in heater.targets]
        assert times[-4:] == [2.5, 3.0, 3.5, 4.0], "every 0.5 s, not only at the 2 s readings"
        assert [v for _, v in heater.targets[-4:]] == [20.5, 21.0, 21.5, 22.0]

    def test_it_stops_when_the_ramp_ends_and_writes_nothing_unchanged(self, rig, clock, heater):
        controller = rig.attach_controller(
            heater.signals["target"], heater.signals["zone"], law=P(kp=0.0)
        )
        clock.advance(2.0)
        _ramp(controller, end=21.0)  # ends at 3 s
        clock.advance(5.0)
        assert controller not in rig._reapplying
        writes = len(heater.targets)
        clock.advance(10.0)
        assert all(v == 21.0 for _, v in heater.targets[writes - 1 :]), "held, not re-applied"

    def test_the_law_is_not_stepped_and_its_timing_is_untouched(self, rig, clock, heater):
        controller = rig.attach_controller(
            heater.signals["target"], heater.signals["zone"], law=PI(kp=1.0, ki=0.1)
        )
        clock.advance(2.0)
        _ramp(controller)
        clock.advance(2.0)  # a reading at 4 s
        law, interval, last = (
            dict(controller.law.state.__dict__),
            controller._step_interval_ns,
            controller._last_step_ns,
        )
        clock.advance(1.5)  # re-applies at 4.5, 5, 5.5: no reading
        assert dict(controller.law.state.__dict__) == law
        assert (controller._step_interval_ns, controller._last_step_ns) == (interval, last)

    def test_it_is_suppressed_in_manual_while_held_and_while_stale(self, rig, clock, heater):
        controller = rig.attach_controller(
            heater.signals["target"], heater.signals["zone"], law=P(kp=0.0)
        )
        clock.advance(2.0)
        _ramp(controller)
        controller.manual()
        writes = len(heater.targets)
        clock.advance(1.5)
        assert len(heater.targets) == writes, "manual: nothing"
        _ramp(controller)
        heater.zone = invalid("crc")
        clock.advance(0.5)  # the reading at 4 s: frozen
        assert controller.held == Code.FROZEN
        writes = len(heater.targets)
        clock.advance(1.5)
        assert len(heater.targets) == writes, "held: nothing"
        assert controller.reapply(clock.now_ns()) is False

    def test_hold_clear_tick_then_a_reading_resumes_bounded(self, rig, clock, heater):
        controller = rig.attach_controller(
            heater.signals["target"], heater.signals["zone"], law=P(kp=1.0)
        )
        clock.advance(2.0)
        _ramp(controller)
        clock.advance(2.0)
        interval = controller._step_interval_ns
        heater.zone = invalid("crc")
        clock.advance(2.0)  # held
        heater.zone = 20.0
        clock.advance(6.0)  # three readings with a value: resumes; re-applies between
        assert controller.held is None and controller.mode is ControllerMode.REGULATING
        assert controller._step_interval_ns == interval

    def test_a_concurrent_delivery_is_serialised(self, fresh):
        """On a scaled clock: re-applies on the timers' thread, readings on the poll's."""
        rig = Rig()
        rig.clock = ScaledClock(speed=20.0)
        heater = Heater(fresh("heater"))
        heater.poll_s = 0.2
        heater.clock = rig.clock  # type: ignore[assignment]
        rig.add_device(heater)
        try:
            controller = rig.attach_controller(
                heater.signals["target"],
                heater.signals["zone"],
                law=P(kp=0.5),
                setpoint_period_s=0.05,
            )
            rig.start_polling(heater)
            until(lambda: heater.signals["zone"] in rig.router.latest)
            with rig.lock:
                _ramp(controller, end=1000.0, per_second=5.0)
            until(lambda: len(heater.targets) > 40)
            assert rig.timers.errors == 0
            values = [v for _, v in heater.targets]
            assert values == sorted(values), "no write lands out of order"
        finally:
            rig.close()

    def test_a_reapplied_tick_is_recorded_with_no_reading(self, rig, clock, heater, tmp_path):
        store = SqliteStore(tmp_path / "s.db")
        controller = rig.attach_controller(
            heater.signals["target"], heater.signals["zone"], law=P(kp=0.0)
        )
        clock.advance(2.0)
        session = rig.start_recording(store).writer.session.id
        _ramp(controller)
        clock.advance(2.0)
        rig.stop_recording()
        ticks = store.ticks(session, controller.name)
        assert [(t.offset_ns / S, t.reapplied) for t in ticks] == [
            (0.5, True),
            (1.0, True),
            (1.5, True),
            (2.0, False),  # the reading at 4 s wins the instant it shares with a re-apply
        ]
        assert all(t.measured is None for t in ticks if t.reapplied)
        store.close()

    def test_setpoint_period_s_is_a_controller_key(self):
        assert ControllerEntry(measured="a.b", setpoint_period_s=0.2).setpoint_period_s == 0.2
        with pytest.raises(ValueError):
            ControllerEntry(measured="a.b", setpoint_period_s=0.0)


# endregion
