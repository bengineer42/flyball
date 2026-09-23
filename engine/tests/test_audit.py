"""The runner's action audit: who did what, in an append-only table outside retention.

Every request whose verb is not read, and every stop (`SIGUSR1` included), is one row: the
verified principal, the method, the route, the status and outcome, the request id -- and, for
a demand, each signal's old, requested and applied value. The rows are written off the
event loop, on the auditor's own thread; a write that fails is logged and never refuses
the action.
"""

from __future__ import annotations

import logging
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from conftest import TestClient
from flyball.interfaces.server import audit as server_audit
from flyball.interfaces.server import create_app, principal, set_rig
from flyball.interfaces.server.auth import Fronted
from flyball.interfaces.server.deps import set_stopper, set_store
from flyball.record import SqliteStore, StoreUnavailableError, audit
from flyball.record.errors import ConstraintError, SchemaError
from flyball.rig import Rig
from flyball.rig.stopping import Actor, InterimStopper
from flyball.runner import stopping as runner_stopping
from flyball.runtime.config import load_rig_config

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"
KEY = bytes(range(32))
AUD = "rig-a"
REQUEST_ID = "0123456789abcdef0123456789abcdef"


def _mint(scp: set[str], sub: str = "local:admin", **extra: Any) -> str:
    now = int(time.time())
    claims = principal.Claims(
        sub=sub,
        sid=extra.pop("sid", "s-1"),
        scp=frozenset(scp),
        kind=extra.pop("kind", "human"),
        aud=AUD,
        cip=extra.pop("cip", "10.0.0.7"),
        sch="https",
        iat=now,
        exp=now + 60,
        **extra,
    )
    return principal.mint(KEY, claims)


def _as(scp: set[str], **extra: Any) -> dict[str, str]:
    return {principal.HEADER: _mint(scp, **extra), "X-Request-Id": REQUEST_ID}


OPERATOR = {"read", "operate"}


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStore]:
    store = SqliteStore(tmp_path / "s.sqlite")
    set_store(store)
    try:
        yield store
    finally:
        server_audit.AUDITOR.flush()
        set_store(None)
        store.close()


@pytest.fixture
def oven() -> Iterator[Rig]:
    rig = load_rig_config(EXAMPLES / "oven.yaml").build(start=False)
    set_rig(rig)
    set_stopper(InterimStopper(rig))
    try:
        yield rig
    finally:
        set_stopper(None)
        set_rig(None)


@pytest.fixture
def http(store: SqliteStore, oven: Rig) -> Iterator[TestClient]:
    with TestClient(create_app(front=Fronted(KEY, AUD))) as client:
        yield client


def _rows(store: SqliteStore) -> list[audit.AuditRow]:
    assert server_audit.AUDITOR.flush(), "the auditor never caught up"
    return audit.actions(store)


# region What is recorded


def test_audit_rows(http, store):
    """A read is not recorded; a demand, a refused one, a denied one and a stop are, one each."""
    before = _rows(store)
    assert http.get("/api/devices", headers=_as(OPERATOR)).status_code == 200
    assert http.get("/api/health", headers=_as({"read"})).status_code == 200
    assert _rows(store) == before

    clamped = http.put("/api/signals/heater.drive", json=150.0, headers=_as(OPERATOR))
    assert clamped.status_code == 200, clamped.text
    again = http.put("/api/devices/heater/demand", json={"drive": 40.0}, headers=_as(OPERATOR))
    assert again.status_code == 200, again.text
    refused = http.put("/api/signals/thermocouple.temperature", json=1.0, headers=_as(OPERATOR))
    assert refused.status_code == 409, refused.text
    denied = http.post("/api/rig/stop", headers=_as({"read"}, sub="local:viewer", sid="s-2"))
    assert denied.status_code == 403, denied.text
    stopped = http.post(
        "/api/rig/stop", json={"reason": "smoke"}, headers=_as(OPERATOR, kind="agent", via="mcp")
    )
    assert stopped.status_code == 200, stopped.text

    rows = _rows(store)[len(before) :]
    assert [(r.method, r.route, r.status, r.outcome) for r in rows] == [
        ("PUT", "/api/signals/{address}", 200, "done"),
        ("PUT", "/api/devices/{name}/demand", 200, "done"),
        ("PUT", "/api/signals/{address}", 409, "refused"),
        ("POST", "/api/rig/stop", 403, "denied"),
        ("POST", "/api/rig/stop", 200, "done"),
    ]
    first, second, bad, no, stop = rows
    assert (first.sub, first.sid, first.kind, first.via, first.cip, first.scheme) == (
        "local:admin",
        "s-1",
        "human",
        "http",
        "10.0.0.7",
        "proxy",
    )
    assert first.path == "/api/signals/heater.drive"
    assert first.request_id == REQUEST_ID
    assert first.writes == {"heater.drive": {"old": None, "requested": 150.0, "applied": 100.0}}
    assert second.writes == {"heater.drive": {"old": 100.0, "requested": 40.0, "applied": 40.0}}
    assert bad.writes == {
        "thermocouple.temperature": {"old": None, "requested": 1.0, "applied": None}
    }
    assert (no.sub, no.sid) == ("local:viewer", "s-2")
    assert (stop.kind, stop.via) == ("agent", "mcp")
    assert stop.detail == {"reason": "smoke"}
    # One boot, a sequence with no gaps, wall-clock time.
    assert {r.boot for r in rows} == {server_audit.AUDITOR.boot}
    assert [r.seq for r in rows] == list(range(rows[0].seq, rows[0].seq + 5))
    assert all(abs(r.time_ns - time.time_ns()) < 60e9 for r in rows)


def test_an_unverified_request_is_not_recorded(http, store):
    """No principal, a bad one, a preflight: the door's (or the front's) business, not an action."""
    before = _rows(store)
    assert http.post("/api/rig/stop").status_code == 401
    assert http.post("/api/rig/stop", headers={principal.HEADER: "v1.x.y"}).status_code == 401
    http.options("/api/rig/stop", headers=_as(OPERATOR))
    assert _rows(store) == before


def test_the_fronts_anonymous_visitor_is_not_recorded(http, store):
    """At a front every caller comes as `proxy`; its visitor with no credential is `anon:`.

    Recording their refusals would let anyone who reaches the front fill the rig's store,
    in a table nothing ever trims -- with `anonymous: none` (no verbs) or `read`.
    """
    before = _rows(store)
    for i, scp in enumerate([set(), {"read"}] * 5):
        visitor = _as(scp, sub="anon:", sid=f"anon-{i}", kind="human")
        stop = http.post("/api/rig/stop", json={"reason": "x" * 400}, headers=visitor)
        assert stop.status_code == 403
        assert http.put("/api/signals/heater.drive", json=1.0, headers=visitor).status_code == 403
        assert http.post("/api/no-such-route", headers=visitor).status_code == 403
    assert _rows(store) == before


def test_a_callers_denied_rows_are_bounded(http, store):
    """An identified caller refused again and again: a few rows a minute, not one each.

    A read-only token looping an acting request is somebody, so it is recorded -- up to
    `DENIED_PER_MINUTE` rows a minute for that caller; a denied demand keeps at most
    `DENIED_WRITES` of the addresses it asked for. Other callers are not held back.
    """
    before = _rows(store)
    viewer = _as({"read"}, sub="token:viewer", sid="t-9")
    for _ in range(50):
        assert http.post("/api/rig/stop", headers=viewer).status_code == 403
    rows = _rows(store)[len(before) :]
    assert len(rows) == server_audit.DENIED_PER_MINUTE
    assert {(r.sub, r.outcome) for r in rows} == {("token:viewer", "denied")}

    other = _as({"read"}, sub="token:other", sid="t-10")
    flood = {f"k{i}": float(i) for i in range(2000)}
    denied = http.put("/api/devices/heater/demand", json=flood, headers=other)
    assert denied.status_code == 403
    (row,) = _rows(store)[len(before) + len(rows) :]
    assert row.sub == "token:other"
    assert row.writes is not None and len(row.writes) == server_audit.DENIED_WRITES
    # An allowed request is never held back by the cap.
    assert http.post("/api/rig/stop", headers=_as(OPERATOR)).status_code == 200
    assert _rows(store)[-1].outcome == "done"


def test_a_request_id_that_is_not_the_fronts_is_replaced(http, store):
    headers = {principal.HEADER: _mint(OPERATOR), "X-Request-Id": "evil\x1b[31m"}
    assert http.post("/api/rig/stop", headers=headers).status_code == 200
    (row,) = _rows(store)[-1:]
    assert len(row.request_id) == 32 and int(row.request_id, 16) >= 0
    assert row.request_id != "evil\x1b[31m"


def test_bare_runner_token_is_recorded_as_the_token(store, oven, monkeypatch):
    monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
    from flyball.runtime.config import AuthConfig

    with TestClient(create_app(AuthConfig(token="s3cret"))) as http:
        answer = http.post("/api/rig/stop", headers={"Authorization": "Bearer s3cret"})
    assert answer.status_code == 200
    (row,) = _rows(store)[-1:]
    assert (row.sub, row.kind, row.scheme, row.via) == ("token:bare", "service", "token", "http")
    assert len(row.request_id) == 32  # no front: the runner makes one


def test_break_glass_stop_is_recorded(store, oven):
    """`SIGUSR1`'s stop is a row too, with the signal's actor; here, the handler's thread."""
    runner_stopping._stop(lambda: InterimStopper(oven))
    (row,) = _rows(store)[-1:]
    assert (row.sub, row.via, row.method, row.route) == (
        "local:signal",
        "signal",
        "SIGNAL",
        "SIGUSR1",
    )
    assert row.outcome == "done" and row.status is None
    assert row.detail is not None and row.detail["reason"] == "SIGUSR1"
    assert abs(row.time_ns - time.time_ns()) < 60e9


# endregion

# region Append-only, outside retention


def _action(**changes: Any) -> audit.Action:
    fields: dict[str, Any] = {
        "time_ns": time.time_ns(),
        "sub": "local:admin",
        "sid": "s",
        "kind": "human",
        "via": "http",
        "cip": "",
        "method": "POST",
        "route": "/api/rig/stop",
        "path": "/api/rig/stop",
        "status": 200,
        "outcome": "done",
    }
    return audit.Action(**(fields | changes))


def test_audit_survives_retention_and_session_delete(tmp_path):
    store = SqliteStore(tmp_path / "s.sqlite")
    try:
        session = store.open_session(0).session
        audit.append(store, [("boot", 1, _action()), ("boot", 2, _action(status=409))])
        store.trim_session(session.id, 10**18)
        store.end_session(session.id, 10)
        store.delete_session(session.id)
        assert [r.seq for r in audit.actions(store)] == [1, 2]
        # Append-only: the store itself refuses to change or delete a row.
        with pytest.raises(ConstraintError), store._transaction() as connection:
            connection.execute("DELETE FROM audit")
        with pytest.raises(ConstraintError), store._transaction() as connection:
            connection.execute("UPDATE audit SET sub = 'someone else'")
        assert [r.sub for r in audit.actions(store)] == ["local:admin", "local:admin"]
    finally:
        store.close()


def test_migration_refuses_newer_schema(tmp_path):
    """A store written by a newer flyball is refused, not opened and misread (adv-blind 8)."""
    path = tmp_path / "s.sqlite"
    SqliteStore(path).close()
    with sqlite3.connect(path) as connection:
        (version,) = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
        connection.execute("UPDATE schema_version SET version = ?", (version + 1,))
    connection.close()
    with pytest.raises(SchemaError, match="newer"):
        SqliteStore(path)


# endregion

# region A failing audit blocks nothing


def test_audit_write_failure_does_not_block_stop(http, store, oven, monkeypatch, caplog):
    def broken(*args: Any, **kwargs: Any) -> None:
        raise StoreUnavailableError("database or disk is full", "s.sqlite")

    monkeypatch.setattr(audit, "append", broken)
    stopped: list[str] = []
    original = InterimStopper.stop

    def counting(self: InterimStopper, actor: Actor, reason: str) -> Any:
        stopped.append(actor.sub)
        return original(self, actor, reason)

    monkeypatch.setattr(InterimStopper, "stop", counting)
    with caplog.at_level(logging.ERROR, logger="flyball.audit"):
        answer = http.post("/api/rig/stop", headers=_as(OPERATOR))
        demand = http.put("/api/signals/heater.drive", json=30.0, headers=_as(OPERATOR))
        runner_stopping._stop(lambda: InterimStopper(oven))
        assert server_audit.AUDITOR.flush()
    assert answer.status_code == 200 and demand.status_code == 200
    assert stopped == ["local:admin", "local:signal"]
    failed = [r for r in caplog.records if "audit write failed" in r.getMessage()]
    assert failed, caplog.text
    # The action is kept in the log, where the store would not take it.
    assert "local:signal" in caplog.text and "/api/signals/heater.drive" in caplog.text


def test_a_full_queue_drops_and_logs_never_waits(caplog):
    """The store stuck behind a lock: recording returns at once; what does not fit is logged."""
    gate = threading.Event()

    def stuck() -> None:
        gate.wait(5)
        return None

    auditor = audit.Auditor(stuck, capacity=2)
    try:
        with caplog.at_level(logging.ERROR, logger="flyball.audit"):
            start = time.monotonic()
            for n in range(10):
                auditor.record(_action(path=f"/n/{n}"))
            took = time.monotonic() - start
        assert took < 0.5, took
        assert "audit queue full" in caplog.text
    finally:
        gate.set()
        auditor.close()


def test_no_store_is_logged_not_raised(caplog):
    auditor = audit.Auditor(lambda: None)
    try:
        with caplog.at_level(logging.WARNING, logger="flyball.audit"):
            auditor.record(_action())
            assert auditor.flush()
        assert "no store" in caplog.text
    finally:
        auditor.close()


# endregion

# region In a real runner


def test_sigusr1_is_audited_in_a_real_runner(tmp_path):
    """The break-glass's row lands in the runner's own store file."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    path = tmp_path / "s.sqlite"
    env = {k: v for k, v in os.environ.items() if not k.startswith("FLYBALL_")}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "flyball.runner",
            str(EXAMPLES / "oven.yaml"),
            "--port",
            str(port),
            "--store",
            str(path),
        ],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _until(lambda: _answers(port), proc)
        proc.send_signal(signal.SIGUSR1)

        def audited() -> bool:
            with sqlite3.connect(path) as connection:
                rows = connection.execute(
                    "SELECT sub, via, route FROM audit WHERE via = 'signal'"
                ).fetchall()
            connection.close()
            return rows == [("local:signal", "signal", "SIGUSR1")]

        _until(audited, proc)
        assert proc.poll() is None
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    assert proc.returncode == 0, proc.stderr.read() if proc.stderr else ""


def _answers(port: int) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/auth", timeout=2):
            return True
    except OSError:
        return False


def _until(check: Any, proc: subprocess.Popen[str], seconds: float = 30.0) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        assert proc.poll() is None, proc.stderr.read() if proc.stderr else ""
        if check():
            return
        time.sleep(0.1)
    raise AssertionError("never happened")


# endregion


def test_boot_ids_differ():
    assert audit.Auditor(lambda: None).boot != audit.Auditor(lambda: None).boot
