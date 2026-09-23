"""Recording routes: start, read, end; the session closes on shutdown."""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.deps import get_rig, set_store
from flyball.record.sqlite import SqliteStore
from flyball.rig import Rig


@pytest.fixture
def client(tmp_path):
    rig = Rig()
    store = SqliteStore(tmp_path / "t.db")
    set_rig(rig)
    set_store(store)
    with TestClient(create_app()) as c:
        yield c, store
    set_rig(None)
    set_store(None)


def test_start_read_end(client):
    c, store = client
    assert c.get("/api/recording").json() is None
    assert c.post("/api/recording/end").status_code == 409

    started = c.post("/api/recording", json={"details": {"name": "run 1"}})
    assert started.status_code == 201
    session = started.json()
    assert session["end_ns"] is None and session["details"] == {"name": "run 1"}
    assert c.get("/api/recording").json()["id"] == session["id"]
    assert c.post("/api/recording").status_code == 409

    ended = c.post("/api/recording/end")
    assert ended.status_code == 200 and ended.json()["id"] == session["id"]
    assert ended.json()["end_ns"] is not None  # the response reflects the close
    assert c.get("/api/recording").json() is None
    assert store.session(session["id"]).end_ns is not None


def test_start_recording_names_the_rig_in_config(tmp_path):
    """A session opened via the API should show a rig name too, like a `--record` runner session."""
    rig = Rig("furnace")
    store = SqliteStore(tmp_path / "t.db")
    set_rig(rig)
    set_store(store)
    with TestClient(create_app()) as c:
        started = c.post("/api/recording").json()
        assert started["config"] == {"name": "furnace"}

        c.post("/api/recording/end")
        explicit = c.post("/api/recording", json={"config": {"name": "override"}}).json()
        assert explicit["config"] == {"name": "override"}  # an explicit config is never overwritten
    set_rig(None)
    set_store(None)


def test_shutdown_closes_the_session(tmp_path):
    rig = Rig()
    store = SqliteStore(tmp_path / "t.db")
    set_rig(rig)
    set_store(store)
    with TestClient(create_app()) as c:
        session_id = c.post("/api/recording").json()["id"]
    assert store.session(session_id).end_ns is not None
    set_rig(None)
    set_store(None)


def test_end_session_route_closes_live_and_orphaned(client):
    c, store = client
    # An orphan: opened straight on the store, as a dead runner would leave it.
    orphan = store.open_session(1_000).session
    live = c.post("/api/recording").json()

    ended = c.post(f"/api/history/sessions/{orphan.id}/end")
    assert ended.status_code == 200 and ended.json()["end_ns"] == 1_000  # no samples: ends at start
    assert c.post(f"/api/history/sessions/{orphan.id}/end").status_code == 409

    ended = c.post(f"/api/history/sessions/{live['id']}/end")
    assert ended.status_code == 200 and ended.json()["end_ns"] is not None
    assert c.get("/api/recording").json() is None  # went through the rig, so the recorder stopped


def test_two_starts_at_once_open_one_session(client, monkeypatch):
    c, store = client
    rig = get_rig()
    start = rig.start_recording

    def slow_start(*args, **kwargs):
        time.sleep(0.2)  # widen the gap between the 409 check and the open
        return start(*args, **kwargs)

    monkeypatch.setattr(rig, "start_recording", slow_start)
    results: list[int] = []
    threads = [
        threading.Thread(target=lambda: results.append(c.post("/api/recording").status_code))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [201, 409], "the second start sees the first's session"
    assert [s for s in store.sessions() if s.kind == "session"][-1].end_ns is None
