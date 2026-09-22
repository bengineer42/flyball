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

from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.deps import STORE_SLOTS, set_store
from flyball.record.sqlite import SqliteStore

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
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
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
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
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
