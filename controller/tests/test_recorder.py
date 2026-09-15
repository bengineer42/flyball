"""Recording: buffered, batched, complete on close."""

from __future__ import annotations

from flyball.control import PI
from flyball.db.sqlite import SqliteStore
from helpers import sample


def test_deliveries_are_buffered_and_written_on_close(rig, probe, temperature, heater, clock):
    rig.attach_loop(probe[temperature], heater, law=PI(kp=1.0))
    rig.loops[heater.name].regulate(10.0)
    store = SqliteStore(":memory:")
    recorder = rig.start_recording(store)
    for i in range(50):
        clock.advance(0.01)
        rig.on_read([sample(probe, temperature, float(i), clock.now_ns(), seq=i + 1)])
    assert len(recorder._ticks) == 50, "nothing flushed inside the interval"
    rig.stop_recording()
    session = store.sessions()[0]
    assert len(store.ticks(session.id, heater.name)) == 50
    assert len(store.series(session.id, str(probe.name), temperature.name).points) == 50


def test_flush_interval_writes_part_way(rig, probe, temperature, heater, clock):
    rig.attach_loop(probe[temperature], heater, law=PI(kp=1.0))
    store = SqliteStore(":memory:")
    recorder = rig.start_recording(store)
    recorder.flush_s = 0.0  # every delivery flushes
    rig.on_read([sample(probe, temperature, 1.0, clock.now_ns())])
    assert recorder._samples == [] and recorder._ticks == []
    rig.stop_recording()
