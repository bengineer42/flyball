"""The runner's action audit: who did what to the rig, kept apart from the recordings.

An [Action][flyball.record.audit.Action] is one thing someone asked the rig to do: the
verified principal (`sub`, `sid`, `kind`, `via`, `cip`), the method and route, the status
and its outcome, the request id and, for a demand, each signal's old, requested and applied
value. The rows go in the `audit` table (migration 0012): wall time, not the rig's clock;
no session, so retention and deleting a session never reach it; append-only, the store
refusing an update or a delete.

An [Auditor][flyball.record.audit.Auditor] writes them on a thread of its own, so the
caller -- the server's event loop, the break-glass signal's thread -- never waits for the
store. If a write fails the action has still happened: the failure is logged, with the
action in full, and nothing is refused. Refusing a stop because the disk is full would be
worse than a gap in the audit, and the gap shows: `seq` counts each runner process's
actions (`boot`) from 1.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import logging
import queue
import secrets
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final, Literal

from .sqlite import SqliteStore
from .store import Store

__all__ = ["Action", "AuditRow", "Auditor", "Outcome", "actions", "append", "outcome"]

log = logging.getLogger("flyball.audit")

Outcome = Literal["done", "denied", "refused", "failed"]
"""`done` (2xx, 3xx); `denied` by the door (401, 403); `refused` by the rig (another 4xx);
`failed` (5xx)."""

Write = Mapping[str, float | None]
"""One signal's demand: `old`, `requested` and `applied`, None where not known."""


def outcome(status: int) -> Outcome:
    """What a response's status says became of the action."""
    if status < 400:
        return "done"
    if status in (401, 403):
        return "denied"
    if status < 500:
        return "refused"
    return "failed"


@dataclasses.dataclass(frozen=True)
class Action:
    """One audited action, as the caller knows it; the auditor numbers it."""

    time_ns: int
    """Wall-clock time it was asked for, ns since the epoch (not the rig's clock)."""
    sub: str
    sid: str
    kind: str
    via: str
    """`http`, `mcp` (a tool's own call) or `signal` (the break-glass)."""
    cip: str
    """The client's address as the front (or, bare, the runner) saw it; empty when unknown."""
    method: str
    """The HTTP method; `SIGNAL` for the break-glass."""
    route: str
    """The route's template (`/api/signals/{address}`), or the signal's name."""
    path: str
    status: int | None
    """The response's status; None for an action that was no request."""
    outcome: Outcome
    name: str = ""
    """The principal's display name (`nm`); never authorises."""
    scheme: str = ""
    """How the caller got in: `proxy`, `token`, `session`, `local`; empty for a signal."""
    request_id: str = ""
    writes: Mapping[str, Write] | None = None
    """For a demand, by signal address."""
    details: Mapping[str, Any] | None = None
    """Anything more: a stop's reason."""

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class AuditRow(Action):
    """An action as the store holds it."""

    id: int = 0
    boot: str = ""
    """The runner process that recorded it."""
    seq: int = 0
    """Its place among `boot`'s actions, from 1."""


_COLUMNS: Final = (
    "time_ns",
    "boot",
    "seq",
    "sub",
    "name",
    "sid",
    "kind",
    "via",
    "cip",
    "scheme",
    "method",
    "route",
    "path",
    "status",
    "outcome",
    "request_id",
    "writes",
    "details",
)


def _dumps(value: Any) -> str | None:
    return None if value is None else json.dumps(value, separators=(",", ":"))


def append(store: Store, actions: Sequence[tuple[str, int, Action]]) -> None:
    """Write `(boot, seq, action)`s in one transaction. Blocks on the store: never on a loop.

    Raises:
        StoreError: As the store classifies it: `StoreUnavailableError` for a store it
            cannot reach, `ConstraintError` for a `(boot, seq)` already written.
        TypeError: `store` is not the SQLite store, the one that has the table.
    """
    if not isinstance(store, SqliteStore):
        raise TypeError(f"no audit table in a {type(store).__name__}")
    rows = [
        (
            a.time_ns,
            boot,
            seq,
            a.sub,
            a.name,
            a.sid,
            a.kind,
            a.via,
            a.cip,
            a.scheme,
            a.method,
            a.route,
            a.path,
            a.status,
            a.outcome,
            a.request_id,
            _dumps(a.writes),
            _dumps(a.details),
        )
        for boot, seq, a in actions
    ]
    marks = ", ".join("?" * len(_COLUMNS))
    with store._transaction() as connection:  # the record package's own store
        connection.executemany(f"INSERT INTO audit ({', '.join(_COLUMNS)}) VALUES ({marks})", rows)


def actions(store: Store, *, after: int = 0, limit: int | None = None) -> list[AuditRow]:
    """The audit's rows in the order they were written, those with an id above `after`."""
    if not isinstance(store, SqliteStore):
        raise TypeError(f"no audit table in a {type(store).__name__}")
    rows = store._query(
        "SELECT * FROM audit WHERE id > ? ORDER BY id LIMIT ?",
        (after, -1 if limit is None else limit),
    )
    return [
        AuditRow(
            **{c: r[c] for c in _COLUMNS if c not in ("writes", "details")},
            writes=None if r["writes"] is None else json.loads(r["writes"]),
            details=None if r["details"] is None else json.loads(r["details"]),
            id=r["id"],
        )
        for r in rows
    ]


class Auditor:
    """Numbers actions and writes them to the store on a thread of its own.

    `record` never blocks and never raises: the action is queued, or, when `capacity`
    are already waiting (the store stuck), logged and dropped -- its `seq` stays taken,
    so the gap shows. `store` is asked for the store at each write; with none attached
    the action is logged instead. One auditor per process: `boot` is its id.
    """

    BATCH: Final = 100

    def __init__(self, store: Callable[[], Store | None], capacity: int = 10_000) -> None:
        self.store = store
        self.boot = secrets.token_hex(8)
        self._seq = itertools.count(1)
        self._numbering = threading.Lock()
        self._queue: queue.Queue[tuple[int, Action] | None] = queue.Queue(capacity)
        self._thread: threading.Thread | None = None
        self._starting = threading.Lock()

    def record(self, action: Action) -> None:
        """Queue `action` for the store; log it if it cannot be. Returns at once."""
        with self._numbering:  # a seq taken is a seq queued, in order
            seq = next(self._seq)
            try:
                self._queue.put_nowait((seq, action))
            except queue.Full:
                log.error("audit queue full; not stored: %s", _line(self.boot, seq, action))
                return
        self._start()

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until everything queued has been written or logged; False on the timeout."""
        if self._thread is None:
            return self._queue.empty()
        done = threading.Event()
        waiter = threading.Thread(target=lambda: (self._queue.join(), done.set()), daemon=True)
        waiter.start()
        return done.wait(timeout)

    def close(self, timeout: float = 5.0) -> None:
        """Write what is queued, then stop the thread."""
        thread = self._thread
        if thread is None:
            return
        self.flush(timeout)
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            return
        thread.join(timeout)
        self._thread = None

    def _start(self) -> None:
        if self._thread is not None:
            return
        with self._starting:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="audit", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while True:
            first = self._queue.get()
            if first is None:
                self._queue.task_done()
                return
            batch = [first]
            stop = False
            while len(batch) < self.BATCH:
                try:
                    more = self._queue.get_nowait()
                except queue.Empty:
                    break
                if more is None:
                    self._queue.task_done()
                    stop = True
                    break
                batch.append(more)
            self._write(batch)
            for _ in batch:
                self._queue.task_done()
            if stop:
                return

    def _write(self, batch: list[tuple[int, Action]]) -> None:
        try:
            store = self.store()
        except Exception:
            store = None
            log.exception("audit: finding the store failed")
        if store is None:
            for seq, action in batch:
                log.warning(
                    "audit: no store attached; not stored: %s", _line(self.boot, seq, action)
                )
            return
        try:
            append(store, [(self.boot, seq, action) for seq, action in batch])
        except Exception:
            log.exception("audit write failed; the actions were done, not stored")
            for seq, action in batch:
                log.error("audit write failed; not stored: %s", _line(self.boot, seq, action))


def _line(boot: str, seq: int, action: Action) -> str:
    """The action as one JSON line: `json.dumps` escapes control characters (CWE-117)."""
    return json.dumps({"boot": boot, "seq": seq, **action.as_dict()}, default=str)
