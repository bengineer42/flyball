"""What a store failure answers: a refused write 409, an unreachable store 503, a bug 500."""

from __future__ import annotations

import sqlite3

import pytest

from conftest import TestClient
from flyball.interfaces.server import create_app
from flyball.interfaces.server.deps import set_store
from flyball.record.errors import ConstraintError, StoreUnavailableError
from flyball.record.sqlite import SqliteStore

TUNING = {"law": "pid", "config": {}, "created_ns": 0}


@pytest.fixture
def store(tmp_path):
    store = SqliteStore(tmp_path / "t.db")
    set_store(store)
    yield store
    set_store(None)
    store.close()


@pytest.fixture
def client(store):
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c


def test_a_foreign_key_violation_is_a_conflict(client):
    r = client.put("/api/history/tunings/gentle", json={**TUNING, "session_id": 999999})
    assert r.status_code == 409
    assert "FOREIGN KEY constraint failed" in r.json()["detail"]


def test_a_write_the_store_refuses_keeps_sqlites_error_as_its_cause(store):
    with pytest.raises(ConstraintError) as caught:
        store.save_tuning("gentle", "pid", {}, 0, 999999)
    assert isinstance(caught.value.__cause__, sqlite3.IntegrityError)


def test_a_locked_store_is_unavailable(client, store):
    # Another process holds the write lock. Wait for it no longer than it takes to fail.
    store._connection.execute("PRAGMA busy_timeout = 0")
    holder = sqlite3.connect(store.path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        r = client.put("/api/history/tunings/gentle", json=TUNING)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert r.status_code == 503
    assert "locked" in r.json()["detail"]
    assert client.put("/api/history/tunings/gentle", json=TUNING).status_code == 201


def test_a_closed_store_is_a_bug_not_an_outage(client, store):
    store.close()
    assert client.get("/api/history/tunings").status_code == 500


def test_a_nested_begin_is_classified_and_leaves_the_store_usable(store):
    with store._transaction(), pytest.raises(StoreUnavailableError), store._transaction():
        pass
    assert store.tunings() == []


def test_a_failed_rollback_does_not_hide_what_the_body_raised(store):
    class Boom(Exception):
        pass

    with pytest.raises(Boom), store._transaction() as connection:
        connection.close()
        raise Boom
