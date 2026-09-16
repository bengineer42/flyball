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


def test_actuator_config_and_source_label_are_recorded(rig, probe, temperature, heater, clock):
    probe.label = "Probe A"
    rig.attach_loop(probe[temperature], heater, law=PI(kp=1.0))
    store = SqliteStore(":memory:")
    rig.start_recording(store)
    rig.on_read([sample(probe, temperature, 1.0, clock.now_ns())])
    rig.stop_recording()
    session = store.sessions()[0]
    (actuator,) = store.actuators(session.id)
    assert actuator.config == heater.config.model_dump(mode="json")
    (source,) = [s for s in store.sources(session.id) if s.name == str(probe.name)]
    assert source.label == "Probe A"


def test_the_writer_thread_flushes_off_the_delivery_path(rig, probe, temperature, heater, clock):
    import time

    rig.attach_loop(probe[temperature], heater, law=PI(kp=1.0))
    store = SqliteStore(":memory:")
    recorder = rig.start_recording(store)
    recorder.flush_s = 0.01
    rig.on_read([sample(probe, temperature, 1.0, clock.now_ns())])
    assert recorder._samples, "buffered on delivery, not written"
    deadline = time.monotonic() + 2
    while recorder._samples and time.monotonic() < deadline:
        time.sleep(0.005)
    assert recorder._samples == [] and recorder._ticks == [], "the thread wrote it"
    assert recorder.running
    rig.stop_recording()
    assert not recorder.running


def test_a_failing_store_stops_recording_and_raises_an_event(rig, probe, temperature, clock):
    import time

    store = SqliteStore(":memory:")
    recorder = rig.start_recording(store, sources=[probe])
    recorder.flush_s = 0.01

    class Broken:  # a session writer whose disk has filled
        def __init__(self, inner):
            self._inner = inner

        def write_samples(self, samples):
            raise OSError("disk full")

        def __getattr__(self, name):
            return getattr(self._inner, name)

    recorder.writer = Broken(recorder.writer)  # type: ignore[assignment]
    rig.on_read([sample(probe, temperature, 1.0, clock.now_ns())])
    deadline = time.monotonic() + 2
    while recorder.failed is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert isinstance(recorder.failed, OSError)
    assert rig.recorder is None, "detached: control goes on unrecorded"
    assert rig.recent[-1].kind == "recording_failed" and "disk full" in rig.recent[-1].message
    rig.on_read([sample(probe, temperature, 2.0, clock.now_ns(), seq=2)])  # still delivers
