"""The store never stalls the server: its lock is waited for on worker threads, not the loop.

Each test holds the store's lock from a thread of its own, as the recorder or a
long delete does, and checks that a request with nothing to do with the store
is answered meanwhile -- with a control that the store request really waited.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator

import httpx
import pytest

from flyball.control import PI
from flyball.foundation.device import WriteState
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.deps import STORE_SLOTS, set_store
from flyball.record import SessionNotFoundError, Tick
from flyball.record.sqlite import SqliteStore
from flyball.record.types import Event, SpanKind
from test_recorder import Furnace, _sample

HOLD_S = 1.0
QUICK_S = 0.3


@pytest.fixture
def store(tmp_path) -> Iterator[SqliteStore]:
    store = SqliteStore(tmp_path / "s.db")
    set_rig(None)
    set_store(store)
    yield store
    set_store(None)
    store.close()


def _hold(store: SqliteStore, held: threading.Event) -> threading.Thread:
    def run() -> None:
        with store._lock:
            held.set()
            time.sleep(HOLD_S)

    thread = threading.Thread(target=run, name="holder")
    thread.start()
    assert held.wait(1)
    return thread


async def _timed(request) -> tuple[float, httpx.Response]:
    start = time.perf_counter()
    response = await request
    return time.perf_counter() - start, response


async def test_a_held_store_lock_does_not_stall_an_unrelated_request(store):
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        held = threading.Event()
        holder = _hold(store, held)
        waiting = asyncio.create_task(_timed(client.get("/api/history/tunings")))
        start = time.perf_counter()
        await asyncio.sleep(0.1)  # the store request is at the lock by now
        health = await client.get("/api/health")
        answered_s = time.perf_counter() - start
        store_was_waiting = not waiting.done()
        store_s, stored = await waiting
        holder.join()

    assert health.status_code == 200
    assert answered_s < 0.1 + QUICK_S, f"/api/health took {answered_s:.3f} s behind the store"
    # The control: the store request was at the lock the whole time, not done early.
    assert store_was_waiting
    assert stored.status_code == 200
    assert store_s >= HOLD_S - 0.2, f"the store request did not wait ({store_s:.3f} s)"


async def test_requests_queued_on_the_store_leave_threads_for_the_rest(store):
    """More store requests than anyio has threads: a sync route elsewhere still gets one."""
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        held = threading.Event()
        holder = _hold(store, held)
        queued = [asyncio.create_task(client.get("/api/history/tunings")) for _ in range(50)]
        await asyncio.sleep(0.2)
        slots_in_use = STORE_SLOTS.borrowed_tokens
        elapsed, auth = await _timed(client.get("/api/auth"))  # a plain `def` route
        pending = sum(not q.done() for q in queued)
        answers = await asyncio.gather(*queued)
        holder.join()

    assert auth.status_code == 200
    assert elapsed < QUICK_S, f"/api/auth took {elapsed:.3f} s: no thread to run on"
    # The control: every store request was still queued, and all the slots taken.
    assert pending == 50
    assert slots_in_use == STORE_SLOTS.total_tokens
    assert all(a.status_code == 200 for a in answers)


# region Deleting a session


DATA = ("sample", "reading", "tick", "write_state", "event", "span", "device", "signal")


def _count(store: SqliteStore, table: str, session_id: int) -> int:
    with store._lock:
        sql = f"SELECT COUNT(*) FROM {table} WHERE session_id = ?"
        return int(store._connection.execute(sql, (session_id,)).fetchone()[0])


def _recorded(store: SqliteStore, rig, fresh, samples: int) -> int:
    """A closed session with `samples` samples of two signals, and ticks, states, events, a span."""
    furnace = Furnace(fresh("furnace"))
    rig.add_device(furnace)
    controller = rig.attach_controller(
        furnace.signals["heater"], furnace.signals["zone1"], law=PI(kp=1.0)
    )
    writer = store.open_session(0)
    writer.declare_device(furnace)
    for signal in furnace.signals.values():
        writer.declare_signal(signal)
    writer.declare_controller(controller)
    writer.write_samples([_sample(furnace, i, 20.0 + i, zone2=1.0) for i in range(samples)])
    heater = furnace.signals["heater"]
    rows = range(0, samples, 10)
    writer.write_ticks([Tick(controller.name, i, "regulate", 1.0) for i in rows])
    for i in rows:
        writer.write_states(i, {heater: WriteState(value=float(i))})
        writer.write_event(Event(i, "note", None, {"i": i}))
    writer.close_span(writer.open_span(SpanKind.PROGRAM, "p", 0), samples)
    writer.end(samples)
    return writer.session.id


def test_a_large_session_deletes_completely_in_many_transactions(tmp_path, rig, fresh, monkeypatch):
    store = SqliteStore(tmp_path / "s.db")
    doomed = _recorded(store, rig, fresh, 3000)
    kept = _recorded(store, rig, fresh, 50)
    store.save_tuning("warm", "PI", {"kp": 1.0}, 0, session_id=doomed)
    before = {t: _count(store, t, doomed) for t in DATA}
    assert all(before.values()), before  # the control: every table had rows to delete
    kept_before = {t: _count(store, t, kept) for t in DATA}

    monkeypatch.setattr(SqliteStore, "_DELETE_ROWS", 100)
    transactions = 0
    inner = SqliteStore._transaction

    def counted(self):
        nonlocal transactions
        transactions += 1
        return inner(self)

    monkeypatch.setattr(SqliteStore, "_transaction", counted)
    store.delete_session(doomed)

    # 6000 readings at 100 a batch, 300 each of ticks, states, events: 60 + 3 x 3, and more.
    assert transactions > 60
    assert {t: _count(store, t, doomed) for t in DATA} == dict.fromkeys(DATA, 0)
    assert [s.id for s in store.sessions()] == [kept]
    assert {t: _count(store, t, kept) for t in DATA} == kept_before
    assert store.tuning("warm").session_id is None  # made in it, outlives it
    assert store.deleting_sessions() == []
    with pytest.raises(SessionNotFoundError):
        store.delete_session(doomed)


def test_a_trim_goes_in_batches_too(tmp_path, rig, fresh, monkeypatch):
    store = SqliteStore(tmp_path / "s.db")
    session = _recorded(store, rig, fresh, 3000)
    monkeypatch.setattr(SqliteStore, "_DELETE_ROWS", 100)
    transactions = 0
    inner = SqliteStore._transaction

    def counted(self):
        nonlocal transactions
        transactions += 1
        return inner(self)

    monkeypatch.setattr(SqliteStore, "_transaction", counted)
    row = store.trim_session(session, 2000)

    assert transactions > 40  # 4000 readings before the cut, at 100 a batch
    assert row.start_ns == 2000
    assert _count(store, "sample", session) == 1000
    assert _count(store, "reading", session) == 2000
    zone1 = next(s.address for s in store.signals(session) if s.address.endswith(".zone1"))
    first = store.series(session, zone1).points[0]
    assert (first.offset_ns, first.value) == (0, 2020.0)  # the sample at 2000, now the start


def test_others_get_the_lock_between_batches(tmp_path, rig, fresh, monkeypatch):
    store = SqliteStore(tmp_path / "s.db")
    doomed = _recorded(store, rig, fresh, 20_000)
    monkeypatch.setattr(SqliteStore, "_DELETE_ROWS", 200)
    running = threading.Event()
    done = threading.Event()
    reads: list[float] = []

    def reader() -> None:
        running.wait()
        while not done.is_set():
            start = time.perf_counter()
            store.sessions()
            reads.append(time.perf_counter() - start)

    thread = threading.Thread(target=reader)
    thread.start()
    start = time.perf_counter()
    running.set()
    store.delete_session(doomed)
    took = time.perf_counter() - start
    done.set()
    thread.join()

    # 40,000 readings at 200 a batch: 200 batches, and a read got in between them.
    assert len(reads) >= 20, f"{len(reads)} reads in {took:.3f} s of deleting"
    assert max(reads) < took / 4, f"a read waited {max(reads):.3f} s of {took:.3f} s"


def test_a_delete_cut_off_part_way_is_marked_and_finished_by_deleting_again(
    tmp_path, rig, fresh, monkeypatch
):
    store = SqliteStore(tmp_path / "s.db")
    doomed = _recorded(store, rig, fresh, 3000)
    monkeypatch.setattr(SqliteStore, "_DELETE_ROWS", 100)
    transactions = 0
    inner = SqliteStore._transaction

    def crash(self):
        nonlocal transactions
        transactions += 1
        if transactions == 40:
            raise KeyboardInterrupt  # the process dies between two batches
        return inner(self)

    monkeypatch.setattr(SqliteStore, "_transaction", crash)
    with pytest.raises(KeyboardInterrupt):
        store.delete_session(doomed)
    monkeypatch.undo()

    (half,) = store.deleting_sessions()
    assert half.id == doomed and half.details["deleting"] is True
    assert doomed in [s.id for s in store.sessions()]  # still listed, marked
    assert 0 < _count(store, "sample", doomed) < 3000  # part-deleted
    store.delete_session(doomed)
    assert store.deleting_sessions() == [] and store.sessions() == []
    assert {t: _count(store, t, doomed) for t in DATA} == dict.fromkeys(DATA, 0)


# endregion
