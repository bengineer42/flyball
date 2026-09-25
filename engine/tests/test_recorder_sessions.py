"""The recorder owns sessions: what produced each, switching without losing a row, the guard."""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest

import flyball
from conftest import TestClient
from flyball.foundation.device import Code, Sample
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.deps import set_store
from flyball.record import SqliteStore
from flyball.rig import Rig
from flyball.runtime import recorder as recorder_module
from flyball.runtime.recorder import Recorder, installed_packages
from test_scratch import Oven, feed

SRC = Path(flyball.__file__).parent


@pytest.fixture
def store(tmp_path):
    store = SqliteStore(tmp_path / "s.sqlite")
    yield store
    store.close()


@pytest.fixture
def oven(rig, fresh):
    oven = Oven(fresh("oven"))
    rig.add_device(oven)
    return oven


def test_a_session_keeps_its_packages_and_a_kept_range_copies_them(store):
    packages = {"flyball": "0.1.0", "flyball-linux": "0.2.0"}
    writer = store.open_session(0, flyball_version="0.1.0", packages=packages)
    session = writer.session
    assert session.packages == packages and store.session(session.id).packages == packages
    writer.end(10)
    kept = store.keep_range(session.id, 2, 8)
    assert (kept.flyball_version, kept.packages) == ("0.1.0", packages)
    assert store.open_session(0).session.packages is None, "none said: none kept"


# region Provenance


def test_installed_packages_are_what_registers_flyball_configs():
    packages = installed_packages()
    assert packages["flyball"] == flyball.__version__, "the engine registers its own built-ins"
    assert "flyball-sim" in packages, "and so does the sim, installed beside it here"


def test_a_session_records_what_produced_it_without_being_told(rig, store):
    recorder = Recorder(rig, store)
    opened = recorder.start_session().writer.session
    recorder.end_session()
    row = store.session(opened.id)
    assert row.flyball_version == flyball.__version__
    assert row.packages == installed_packages() and row.hardware is None


def test_over_the_api_a_client_adds_to_the_runner_s_own_and_replaces_none_of_it(
    tmp_path, monkeypatch
):
    rig = Rig("oven")
    store = SqliteStore(tmp_path / "t.db")
    set_rig(rig)
    set_store(store)
    try:
        with TestClient(create_app()) as c:
            bare = c.post("/api/recording").json()
            assert bare["flyball_version"] == flyball.__version__
            assert bare["packages"] == installed_packages() and bare["hardware"] is None
            c.post("/api/recording/end")
            posted = c.post(
                "/api/recording", json={"flyball_version": "9.9", "hardware": "bench 2"}
            ).json()
            assert posted["flyball_version"] == flyball.__version__, "a client's is not kept"
            assert posted["hardware"] == "bench 2", "the rig says nothing: the client's stands"
            c.post("/api/recording/end")
            # Once the rig can say what it runs on, a client's hardware adds to it.
            monkeypatch.setattr(recorder_module, "rig_hardware", lambda rig: {"board": "pi5"})
            both = c.post("/api/recording", json={"hardware": {"board": "mine", "bench": 2}}).json()
            assert both["hardware"] == {"board": "pi5", "bench": 2}
            c.post("/api/recording/end")
            other = c.post("/api/recording", json={"hardware": "bench 2"}).json()
            assert other["hardware"] == {"rig": {"board": "pi5"}, "posted": "bench 2"}
    finally:
        set_store(None)
        set_rig(None)
        store.close()


def test_a_rotation_carries_the_provenance_on(rig, oven, clock, store):
    recorder = Recorder(rig, store)
    first = recorder.start_session(hardware="bench 2").writer.session
    clock.advance(10)
    second = recorder.rotate(5_000_000_000)
    assert second is not None and second.writer.session.continues == first.id
    row = store.session(second.writer.session.id)
    assert (row.flyball_version, row.packages, row.hardware) == (
        flyball.__version__,
        installed_packages(),
        "bench 2",
    )
    recorder.stop()


# endregion

# region Switching sessions


def test_a_switch_puts_every_row_on_one_side_of_it(rig, oven, clock, store):
    """Deliveries on another thread while sessions switch: each reading lands exactly once."""
    recorder = Recorder(rig, store)
    first = recorder.start_session(kind="scratch").writer.session
    stop = threading.Event()
    fed = []

    def deliver() -> None:
        zone = oven.signals["zone"]
        while not stop.is_set():
            clock.advance(0.001)
            fed.append(clock.now_ns())
            rig.on_samples([Sample(oven.root, clock.now_ns(), {zone: float(len(fed))})])
            time.sleep(0.0002)  # a fast poll, not a flood the store cannot keep up with

    feeder = threading.Thread(target=deliver)
    feeder.start()
    ids = [first.id]
    try:
        for _ in range(10):
            time.sleep(0.005)
            ids.append(recorder.start_session().writer.session.id)
    finally:
        stop.set()
        feeder.join()
    recorder.stop()
    address = f"{oven.name}.zone"
    recorded = [p.value for sid in ids for p in store.series(sid, address).points]
    assert len(fed) > 20 and len(set(ids)) == 11, "deliveries ran across the switches"
    assert sorted(recorded) == [float(i) for i in range(1, len(fed) + 1)], "none lost, none twice"


def test_a_failed_session_lets_go_and_the_next_one_starts_clean(rig, oven, store):
    recorder = Recorder(rig, store)
    opened = recorder.start_session()
    assert rig.sink is recorder
    opened.on_failure(OSError("disk full"))  # type: ignore[misc]  # as its thread would
    assert recorder.session is None and rig.sink is None, "control goes on unrecorded"
    assert rig.recent[-1].code == Code.RECORDING_FAILED
    assert store.session(opened.writer.session.id).end_ns is not None, "ended where it failed"
    again = recorder.start_session()
    assert rig.sink is recorder and recorder.session is again
    assert not [c for c in rig.conditions.of(rig) if c.code == Code.RECORDING_FAILED]
    recorder.stop()


def test_the_rig_closing_leaves_the_recorder_to_end_its_session(rig, oven, clock, store):
    recorder = Recorder(rig, store)
    opened = recorder.start_session().writer.session
    feed(rig, oven, clock, 3)
    rig.close()
    assert rig.sink is None
    recorder.stop()
    row = store.session(opened.id)
    assert row.end_ns is not None and len(store.series(opened.id, f"{oven.name}.zone").points) == 3


def test_flush_is_a_barrier_for_a_read_of_what_was_just_recorded(rig, oven, clock, store):
    recorder = Recorder(rig, store)
    opened = recorder.start_session().writer.session
    feed(rig, oven, clock, 2)
    recorder.flush()
    assert len(store.series(opened.id, f"{oven.name}.zone").points) == 2
    recorder.stop()


# endregion

# region The guard


def test_the_rig_does_not_record():
    for gone in ("start_recording", "stop_recording", "recorder", "recording"):
        assert not hasattr(Rig, gone), f"Rig.{gone} is the recorder's now"
    assert not hasattr(Rig(), "on_recording_stopped")


def test_only_the_recorder_opens_a_session_on_the_store():
    """Every `open_session(` in the engine is the store's own or the recorder's."""
    allowed = {SRC / "runtime" / "recorder.py", SRC / "record" / "store.py"}
    calls = [
        f"{path.relative_to(SRC)}:{n}"
        for path in SRC.rglob("*.py")
        if path not in allowed and path.parent != SRC / "record"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if re.search(r"\.open_session\(", line) and "door." not in line
    ]
    assert calls == []


# endregion
