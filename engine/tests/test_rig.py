"""The rig around a delivery: events, recording, and blocking devices on writer threads."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Mapping

import pytest

from flyball.control.laws import P
from flyball.foundation.device import Node, Sample, Severity, Signal, WriteState
from flyball.foundation.errors import ConflictError
from flyball.model.law import Transfer
from flyball.rig.sink import Delivered, Marker, Published, Row, tick_row
from test_rig_devices import Furnace


class FakeSink:
    """The record sink, as the rig puts to it; keeps every row."""

    def __init__(self) -> None:
        self.rows: list[Row] = []

    def put(self, row: Row) -> None:
        self.rows.append(row)

    @property
    def delivered(self) -> list[Delivered]:
        return [r for r in self.rows if isinstance(r, Delivered)]

    @property
    def events(self) -> list:
        return [r.event for r in self.rows if isinstance(r, Published)]


@pytest.fixture
def sink(rig) -> FakeSink:
    sink = FakeSink()
    rig.attach_sink(sink)
    return sink


@pytest.fixture
def furnace(rig, fresh) -> Furnace:
    furnace = Furnace(fresh("furnace"))
    rig.add_device(furnace)
    return furnace


def test_an_event_is_kept_and_recorded(rig, clock):
    clock.advance(2.0)
    event = rig.event(Severity.WARNING, "device", "x", "slow", "read took 3 s", {"took": 3})
    assert rig.recent[-1] is event and event.time_ns == clock.now_ns()
    assert (
        event.severity is Severity.WARNING and event.code == "slow" and event.details == {"took": 3}
    )
    sink = FakeSink()
    rig.attach_sink(sink)
    other = rig.event(Severity.INFO, "rig", "x", "restarted", "polling again")
    assert sink.events == [other]


class TestTheSink:
    """What the rig puts to its one sink: rows made under its lock, numbered in one order."""

    def test_a_delivery_is_one_row_of_what_published_the_ticks_and_the_states(
        self, rig, furnace, sink
    ):
        heater1, zone1 = furnace.signals["heater1"], furnace.signals["zone1"]
        setting = furnace.signals["setpoint"]
        controller = rig.attach_controller(heater1, zone1, law=P(kp=10.0))
        rig.on_samples([Sample(furnace.root, 5, {zone1: 40.0, setting: 1.0})])
        (first,) = sink.delivered
        assert first.samples == (Sample(furnace.root, 5, {zone1: 40.0}),), "only what published"
        assert first.ticks == (tick_row(controller, rig.latest[zone1], 5),) and first.time_ns == 5
        assert first.states == (), "manual: the tick wrote nothing"
        controller.regulate(50.0, transfer=Transfer.COLD)
        rig.on_samples([Sample(furnace.root, 6, {zone1: 40.0})])
        # The delivery itself, then a follow-up delivery for heater1's own readback (it
        # publishes now: the rig pushes what the driver did not).
        row = sink.delivered[-2]
        assert row.time_ns == 6 and [t.controller for t in row.ticks] == [controller.name]
        assert row.states == ((heater1, WriteState(value=100.0, controller=controller.name)),)
        rig.on_samples([Sample(furnace.root, 7, {setting: 2.0})])
        last = sink.delivered[-1]
        assert (last.samples, last.ticks, last.states, last.time_ns) == ((), (), (), 7)
        seqs = [r.seq for r in sink.rows]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "one order"

    def test_a_tick_row_is_the_controller_as_it_was_then(self, rig, furnace, sink):
        heater1, zone1 = furnace.signals["heater1"], furnace.signals["zone1"]
        controller = rig.attach_controller(heater1, zone1, law=P(kp=10.0))
        controller.regulate(50.0, transfer=Transfer.COLD)
        rig.on_samples([Sample(furnace.root, 5, {zone1: 40.0})])
        (tick,) = next(r for r in sink.delivered if r.ticks).ticks
        assert (tick.mode, tick.setpoint, tick.output_value) == ("regulating", 50.0, 100.0)
        controller.manual()
        assert (tick.mode, tick.output_value) == ("regulating", 100.0), "taken then, not later"

    def test_a_manual_demand_is_a_row_at_once(self, rig, furnace, clock, sink):
        heater2 = furnace.signals["heater2"]
        clock.advance(3.0)
        rig.write(furnace.root, {"heater2": 7000.0})
        first = sink.delivered[0]
        assert first.states == (
            (heater2, WriteState(value=6000.0, requested=7000.0, at_limit="high")),
        )
        assert first.time_ns == 3_000_000_000 and (first.samples, first.ticks) == ((), ())
        assert len(sink.delivered) == 2, "heater2's own readback is a follow-up row"

    def test_a_marker_keeps_its_place_in_the_stream(self, rig, furnace, sink):
        zone1 = furnace.signals["zone1"]
        rig.on_samples([Sample(furnace.root, 5, {zone1: 40.0})])
        seq = rig.mark("switch here")
        rig.on_samples([Sample(furnace.root, 6, {zone1: 41.0})])
        before, marker, after = sink.rows
        assert isinstance(marker, Marker) and (marker.seq, marker.message) == (seq, "switch here")
        assert before.seq < seq < after.seq
        assert [r.time_ns for r in (before, after) if isinstance(r, Delivered)] == [5, 6]

    def test_one_sink_at_a_time_and_detach_leaves_another_alone(self, rig, sink):
        other = FakeSink()
        with pytest.raises(ConflictError, match="one recorder per rig"):
            rig.attach_sink(other)
        rig.attach_sink(sink)  # the one attached: nothing to do
        assert rig.detach_sink(other) is None and rig.sink is sink
        assert rig.detach_sink() is sink and rig.sink is None
        rig.event(Severity.INFO, "rig", "x", "quiet", "nobody listening")
        assert sink.events == []

    def test_an_event_off_the_lock_is_numbered_and_put_in_one_step(self, rig):
        """A slow sink holds up the next row, so the sink sees numbers in order (B1)."""
        seen: list[int] = []

        class Slow(FakeSink):
            def put(self, row: Row) -> None:
                if isinstance(row, Published) and row.event.code == "slow":
                    time.sleep(0.1)
                seen.append(row.seq)

        rig.attach_sink(Slow())
        thread = threading.Thread(
            target=lambda: rig.event(Severity.INFO, "rig", "x", "slow", "off the lock")
        )
        thread.start()
        time.sleep(0.03)
        rig.mark("meanwhile")
        thread.join()
        assert len(seen) == 2 and seen == sorted(seen)


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
        assert rig.conditions.of(slow) == []
        rig.close()

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
        rig.close()

    def test_a_failing_bus_is_a_condition_and_an_event_until_a_write_succeeds(
        self, rig, fresh, clock
    ):
        slow = Slow(fresh("flaky"))
        slow.fail = True
        slow.gate.set()
        rig.add_device(slow)
        heater1 = slow.signals["heater1"]
        rig.write(slow.root, {"heater1": 1.0})
        _wait_until(lambda: rig.conditions.of(slow) != [])
        [condition] = rig.conditions.of(slow)
        assert condition.subject == slow.name and condition.code == "write_failed"
        assert condition.severity is Severity.ERROR and "bus timeout" in condition.message
        assert rig.recent[-1].code == "write_failed" and rig.recent[-1].subject == slow.name
        rig.write(slow.root, {"heater1": 2.0})
        _wait_until(lambda: slow.attempts == 2)
        assert len(rig.recent) == 1, "one event per outage, not one per write"
        slow.fail = False
        rig.write(slow.root, {"heater1": 3.0})
        _wait_until(lambda: rig.conditions.of(slow) == [])
        last = rig.recent[-1]
        assert (last.code, last.edge) == ("write_failed", "cleared")
        assert slow.written[heater1].value == 3.0
        rig.close()

    def test_a_synchronous_device_is_written_in_the_delivery(self, rig, furnace):
        heater1, zone1 = furnace.signals["heater1"], furnace.signals["zone1"]
        controller = rig.attach_controller(heater1, zone1, law=P(kp=10.0))
        rig.on_samples([Sample(furnace.root, 0, {zone1: 40.0})])
        controller.regulate(50.0, transfer=Transfer.COLD)
        rig.on_samples([Sample(furnace.root, 1, {zone1: 40.0})])
        assert furnace.inputs == {"heater1": 100.0} and rig._writers == {}


def test_stop_ends_polling_writers_and_recording(rig, fresh, sink):
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
    assert rig.polling.run(polled.name).running is True
    before = len(sink.rows)
    rig.close()
    assert rig.polling.run(polled.name).running is False
    assert rig.sink is None, "recording ended: the sink is let go"
    rig.event(Severity.INFO, "rig", "x", "late", "after close")
    assert all(e.code != "late" for e in sink.events[before:]), "nothing reaches it after"
    assert not rig._writers[slow]._thread.is_alive()
