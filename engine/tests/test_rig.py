"""The rig around a delivery: events, recording, and blocking devices on writer threads."""

from __future__ import annotations

import sys
import threading
import time
import types
from collections.abc import Iterator, Mapping

import pytest

from flyball.control.laws import P
from flyball.foundation.device import Access, Level, Node, Sample, Signal, SignalSpec, WriteState
from flyball.model.law import Transfer
from test_rig_devices import TEMP, Furnace


class FakeWriter:
    def __init__(self) -> None:
        self.ended: list[int] = []

    def end(self, end_ns: int) -> None:
        self.ended.append(end_ns)


class FakeStore:
    def __init__(self) -> None:
        self.sessions: list[tuple[int, dict]] = []

    def open_session(self, start_ns: int, **session) -> FakeWriter:
        self.sessions.append((start_ns, session))
        return FakeWriter()


class FakeRecorder:
    """The recorder contract, as the rig calls it; records every call."""

    made: list[FakeRecorder] = []

    def __init__(self, writer, signals, controllers, flush_s=0.1, on_failure=None) -> None:
        self.writer = writer
        self.signals = list(signals)
        self.controllers = list(controllers)
        self.on_failure = on_failure
        self.records: list[tuple[list, list, dict, int | None]] = []
        self.events: list = []
        self.closed: list[int] = []
        FakeRecorder.made.append(self)

    def record(self, samples, ticks, states, *, time_ns=None) -> None:
        self.records.append((list(samples), list(ticks), dict(states), time_ns))

    def event(self, event) -> None:
        self.events.append(event)

    def close(self, end_ns: int) -> None:
        self.closed.append(end_ns)


@pytest.fixture
def recorder_module(monkeypatch) -> type[FakeRecorder]:
    """`flyball.runtime.recorder` as the rig imports it, replaced by the fake."""
    module = types.ModuleType("flyball.runtime.recorder")
    module.Recorder = FakeRecorder  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "flyball.runtime.recorder", module)
    FakeRecorder.made.clear()
    return FakeRecorder


@pytest.fixture
def furnace(rig, fresh) -> Furnace:
    furnace = Furnace(fresh("furnace"))
    rig.add_device(furnace)
    return furnace


def test_an_event_is_kept_and_recorded(rig, clock, recorder_module):
    clock.advance(2.0)
    event = rig.event(Level.WARNING, "device", "x", "slow", "read took 3 s", {"took": 3})
    assert rig.recent[-1] is event and event.time_ns == clock.now_ns()
    assert event.level is Level.WARNING and event.kind == "slow" and event.details == {"took": 3}
    rig.start_recording(FakeStore())
    other = rig.event(Level.INFO, "rig", "x", "restarted", "polling again")
    assert recorder_module.made[-1].events == [other]


class TestRecording:
    def test_start_records_every_p_or_w_signal_and_every_controller_by_default(
        self, rig, fresh, clock, recorder_module
    ):
        class WithSerial(Furnace):
            # A literal TREE now adds to the parent's; no need to splice `*Furnace.TREE` in.
            TREE = (SignalSpec(name="serial", quantity=TEMP, access=Access.R),)

        furnace = WithSerial(fresh("furnace"))
        rig.add_device(furnace)
        heater1, zone1 = furnace.signals["heater1"], furnace.signals["zone1"]
        controller = rig.attach_controller(heater1, zone1, law=P(kp=10.0))
        store = FakeStore()
        clock.advance(1.0)
        recorder = rig.start_recording(store, name="run 1")
        assert rig.recorder is recorder and store.sessions == [(clock.now_ns(), {"name": "run 1"})]
        assert recorder.signals == [s for s in furnace.signals.values() if s.name != "serial"], (
            "what publishes or is written; an R-only signal is neither"
        )
        assert recorder.controllers == [controller]
        assert recorder.on_failure == rig._recording_failed

        chosen = rig.start_recording(store, signals=[zone1], controllers=[])
        assert recorder.closed == [clock.now_ns()], "replaced: the first session is closed"
        assert chosen.signals == [zone1] and chosen.controllers == []
        rig.stop_recording()
        assert rig.recorder is None and chosen.closed == [clock.now_ns()]
        rig.stop_recording()  # nothing running: nothing to do

    def test_a_delivery_records_what_published_the_ticks_and_the_states(
        self, rig, furnace, recorder_module
    ):
        heater1, zone1 = furnace.signals["heater1"], furnace.signals["zone1"]
        setting = furnace.signals["setpoint"]
        controller = rig.attach_controller(heater1, zone1, law=P(kp=10.0))
        recorder = rig.start_recording(FakeStore())
        rig.on_samples([Sample(furnace.root, 5, {zone1: 40.0, setting: 1.0})])
        assert recorder.records == [
            ([Sample(furnace.root, 5, {zone1: 40.0})], [(controller, rig.latest[zone1])], {}, 5)
        ], "manual: the tick wrote nothing; the setting is not published"
        controller.regulate(50.0, transfer=Transfer.COLD)
        rig.on_samples([Sample(furnace.root, 6, {zone1: 40.0})])
        # The delivery itself, then a follow-up delivery for heater1's own readback (it
        # publishes now: the rig pushes what the driver did not).
        samples, ticks, states, time_ns = recorder.records[-2]
        assert time_ns == 6 and [c for c, _ in ticks] == [controller]
        assert states == {
            heater1: WriteState(value=100.0, controller=controller.name),
        }
        rig.on_samples([Sample(furnace.root, 7, {setting: 2.0})])
        assert recorder.records[-1] == ([], [], {}, 7), "nothing published, nothing written"

    def test_a_manual_demand_records_its_states_at_once(self, rig, furnace, clock, recorder_module):
        heater2 = furnace.signals["heater2"]
        recorder = rig.start_recording(FakeStore())
        clock.advance(3.0)
        rig.write(furnace.root, {"heater2": 7000.0})
        assert recorder.records[0] == (
            [],
            [],
            {heater2: WriteState(value=6000.0, requested=7000.0, at_limit="high")},
            3_000_000_000,
        )
        assert len(recorder.records) == 2, "heater2's own readback is recorded as a follow-up"

    def test_a_failed_recorder_is_detached_and_reported(self, rig, clock, recorder_module):
        recorder = rig.start_recording(FakeStore())
        recorder.on_failure(OSError("disk full"))
        assert rig.recorder is None
        assert rig.recent[-1].kind == "recording_failed" and "disk full" in rig.recent[-1].message
        assert recorder.writer.ended == [clock.now_ns()] and recorder.closed == []
        assert recorder.events == [], "the event did not go back to the failed recorder"


class Slow(Furnace):
    """A furnace whose commit waits on a gate, as a bus with a long timeout would."""

    blocking = True

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.gate = threading.Event()
        self.fail = False
        self.attempts = 0
        self.committed: list[dict[str, float]] = []

    def commit(self, time_ns: int) -> Mapping[Signal, WriteState]:
        self.gate.wait(2)  # a 2 s bus timeout, unless released
        self.attempts += 1
        if self.fail:
            raise OSError("bus timeout")
        self.committed.append({s.name: v for s, v in self.staged.items()})
        return super().commit(time_ns)


def _wait_until(condition, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.005)


class TestBlockingDevices:
    """A blocking device's applies and commits happen on its own thread.

    A failing bus is a condition and an event; the write states arrive
    when the write completes.
    """

    def test_a_delivery_never_waits_and_the_states_arrive_later(self, rig, fresh, clock):
        slow = Slow(fresh("slow"))
        rig.add_device(slow)
        heater1, zone1 = slow.signals["heater1"], slow.signals["zone1"]
        controller = rig.attach_controller(heater1, zone1, law=P(kp=10.0))
        rig.on_samples([Sample(slow.root, 0, {zone1: 40.0})])
        controller.regulate(50.0, transfer=Transfer.COLD)
        assert controller.expected is None, "the write is queued, not done"
        started = time.monotonic()
        with rig.write_states.watch(), rig.controller_states.watch():
            rig.on_samples([Sample(slow.root, 1_000_000_000, {zone1: 40.0})])
            assert time.monotonic() - started < 0.2, "the delivery did not wait on the bus"
            assert slow.commits == 0 and slow.committed == []
            slow.gate.set()
            _wait_until(lambda: heater1 in slow.written)
        assert slow.committed == [{"heater1": 100.0}], "the newest pending value, once"
        expected = WriteState(value=100.0, controller=controller.name)
        assert slow.written[heater1] == expected and controller.expected == 100.0
        assert controller.delivered_correction == 100.0, "delivered() closed the tick"
        assert rig.write_states.changed_since(0)[1] == {heater1.address: expected}
        assert rig.controller_states.changed_since(0)[1][controller.name].expected == 100.0
        assert rig.write_conditions() == []
        rig.stop()

    def test_a_manual_demand_returns_nothing_and_the_state_follows(self, rig, fresh, clock):
        slow = Slow(fresh("slow"))
        rig.add_device(slow)
        heater2 = slow.signals["heater2"]
        assert rig.write(slow.root, {"heater2": 7000.0}) == {}
        assert rig.write(slow.root, {"heater2": 100.0}) == {}
        slow.gate.set()
        _wait_until(lambda: heater2 in slow.written)
        assert slow.committed == [{"heater2": 100.0}], "the newest value wins"
        assert slow.written[heater2] == WriteState(value=100.0)
        rig.stop()

    def test_a_failing_bus_is_a_condition_and_an_event_until_a_write_succeeds(
        self, rig, fresh, clock
    ):
        slow = Slow(fresh("flaky"))
        slow.fail = True
        slow.gate.set()
        rig.add_device(slow)
        heater1 = slow.signals["heater1"]
        rig.write(slow.root, {"heater1": 1.0})
        _wait_until(lambda: rig.write_conditions() != [])
        [(name, condition)] = rig.write_conditions()
        assert name == slow.name and condition.kind == "write_failed"
        assert condition.level is Level.ERROR and "bus timeout" in condition.message
        assert rig.recent[-1].kind == "write_failed" and rig.recent[-1].subject == slow.name
        rig.write(slow.root, {"heater1": 2.0})
        _wait_until(lambda: slow.attempts == 2)
        assert len(rig.recent) == 1, "one event per outage, not one per write"
        slow.fail = False
        rig.write(slow.root, {"heater1": 3.0})
        _wait_until(lambda: rig.write_conditions() == [])
        assert rig.recent[-1].kind == "write_recovered" and slow.written[heater1].value == 3.0
        rig.stop()

    def test_a_synchronous_device_is_written_in_the_delivery(self, rig, furnace):
        heater1, zone1 = furnace.signals["heater1"], furnace.signals["zone1"]
        controller = rig.attach_controller(heater1, zone1, law=P(kp=10.0))
        rig.on_samples([Sample(furnace.root, 0, {zone1: 40.0})])
        controller.regulate(50.0, transfer=Transfer.COLD)
        rig.on_samples([Sample(furnace.root, 1, {zone1: 40.0})])
        assert furnace.inputs == {"heater1": 100.0} and rig._writers == {}


def test_stop_ends_polling_writers_and_recording(rig, fresh, recorder_module):
    class Polled(Furnace):
        def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
            yield from super().read(time_ns, node)

    polled = Polled(fresh("polled"))
    polled.poll_s = 1.0
    rig.add_device(polled)
    rig.start_polling(polled)
    slow = Slow(fresh("slow"))
    slow.gate.set()
    rig.add_device(slow)
    rig.write(slow.root, {"heater1": 1.0})
    recorder = rig.start_recording(FakeStore())
    assert rig.polling.run(polled.name).running is True
    rig.stop()
    assert rig.polling.run(polled.name).running is False
    assert rig.recorder is None and recorder.closed
    assert not rig._writers[slow]._thread.is_alive()
