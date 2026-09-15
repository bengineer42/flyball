"""The SQLite store.

One connection per :class:`SqliteStore`, guarded by a lock, so a store may be
shared across threads; the rig's writer and the server's reader normally each
open their own against the same file, and WAL lets them overlap. Session
declarations are interned in the writer so the hot path -- a delivery of
samples -- is one ``executemany`` per table with integer keys already known.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any

from flyball.core.reading import Channel, Measurand, Sample, Source

from .errors import NotDeclaredError, SessionEndedError, SessionNotFoundError, TuningNotFoundError
from .migrate import migrate
from .types import (
    ActuatorRow,
    ChannelRow,
    Downsample,
    Event,
    LoopRow,
    MeasurandRow,
    Point,
    Series,
    SessionRow,
    SourceRow,
    Span,
    SpanKind,
    Tick,
    TuningRow,
    Window,
)

# region Helpers


def _dumps(value: Any) -> str | None:
    return None if value is None else json.dumps(value, separators=(",", ":"))


def _loads(text: str | None) -> Any:
    return None if text is None else json.loads(text)


def _window_clause(window: Window | None, column: str) -> tuple[str, list[int]]:
    """SQL and parameters restricting ``column`` to the window; empty when unbounded."""
    if window is None:
        return "", []
    clauses, params = [], []
    if window.start_ns is not None:
        clauses.append(f"{column} >= ?")
        params.append(window.start_ns)
    if window.end_ns is not None:
        clauses.append(f"{column} < ?")
        params.append(window.end_ns)
    return "".join(f" AND {c}" for c in clauses), params


def _tuning_row(row: sqlite3.Row) -> TuningRow:
    return TuningRow(
        id=row["id"],
        name=row["name"],
        law=row["law"],
        config=_loads(row["config"]),
        created_ns=row["created_ns"],
        session_id=row["session_id"],
        loop=row["loop"],
        notes=_loads(row["notes"]),
    )


def _session_row(row: sqlite3.Row) -> SessionRow:
    return SessionRow(
        id=row["id"],
        start_ns=row["start_ns"],
        end_ns=row["end_ns"],
        version=row["version"],
        config=_loads(row["config"]),
        hardware=_loads(row["hardware"]),
        details=_loads(row["details"]),
    )


# endregion


class SqliteSessionWriter:
    """Appends to one session. Not thread-safe on its own; the store's lock covers it."""

    __slots__ = ("_actuators", "_ended", "_loops", "_measurands", "_session", "_sources", "_store")

    def __init__(self, store: SqliteStore, session: SessionRow) -> None:
        self._store = store
        self._session = session
        self._sources: dict[Source, int] = {}
        self._measurands: dict[Measurand, int] = {}
        self._actuators: set[str] = set()
        self._loops: set[str] = set()
        self._ended = False

    @property
    def session(self) -> SessionRow:
        return self._session

    def _open(self) -> None:
        if self._ended:
            raise SessionEndedError(self._session.id)

    # region Declarations

    def _measurand_id(self, measurand: Measurand, connection: sqlite3.Connection) -> int:
        if (qid := self._measurands.get(measurand)) is None:
            qid = len(self._measurands) + 1
            connection.execute(
                "INSERT INTO measurand (session_id, id, name, unit, label) VALUES (?, ?, ?, ?, ?)",
                (self._session.id, qid, measurand.name, measurand.unit.symbol, measurand.label),
            )
            self._measurands[measurand] = qid
        return qid

    def declare_source(self, source: Source, kind: str | None = None) -> None:
        self._open()
        if source in self._sources:
            return
        with self._store._transaction() as connection:
            sid = len(self._sources) + 1
            connection.execute(
                "INSERT INTO source (session_id, id, name, kind) VALUES (?, ?, ?, ?)",
                (self._session.id, sid, str(source.name), kind),
            )
            connection.executemany(
                "INSERT INTO channel (session_id, source_id, measurand_id) VALUES (?, ?, ?)",
                [
                    (self._session.id, sid, self._measurand_id(channel.measurand, connection))
                    for channel in source.channels
                ],
            )
            self._sources[source] = sid

    def declare_actuator(self, name: str, kind: str, config: Any = None) -> None:
        self._open()
        if name in self._actuators:
            return
        with self._store._transaction() as connection:
            connection.execute(
                "INSERT INTO actuator (session_id, name, kind, config) VALUES (?, ?, ?, ?)",
                (self._session.id, name, kind, _dumps(config)),
            )
            self._actuators.add(name)

    def declare_loop(self, name: str, channel: Channel, config: Any = None) -> None:
        self._open()
        if name in self._loops:
            return
        if name not in self._actuators:
            raise NotDeclaredError("actuator", name)
        sid = self._sources.get(channel.source)
        if sid is None:
            raise NotDeclaredError("source", str(channel.source.name))
        with self._store._transaction() as connection:
            connection.execute(
                "INSERT INTO loop (session_id, name, source_id, measurand_id, config)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    self._session.id,
                    name,
                    sid,
                    self._measurand_id(channel.measurand, connection),
                    _dumps(config),
                ),
            )
            self._loops.add(name)

    # endregion

    # region Data

    def write_samples(self, samples: Iterable[Sample]) -> None:
        self._open()
        session_id = self._session.id
        sample_rows: list[tuple[int, int, int, int]] = []
        reading_rows: list[tuple[int, int, int, int, int, float]] = []
        for sample in samples:
            if (sid := self._sources.get(sample.source)) is None:
                raise NotDeclaredError("source", str(sample.source.name))
            offset = sample.time_ns - self._session.start_ns
            sample_rows.append((session_id, sid, sample.seq, offset))
            for measurand, value in sample.values.items():
                if (qid := self._measurands.get(measurand)) is None:
                    raise NotDeclaredError("measurand", measurand.name)
                if value == value:  # NaN is a fault, not a reading; the writer routes those
                    reading_rows.append((session_id, sid, sample.seq, qid, offset, value))
        if not sample_rows:
            return
        with self._store._transaction() as connection:
            connection.executemany(
                "INSERT INTO sample (session_id, source_id, seq, offset_ns) VALUES (?, ?, ?, ?)",
                sample_rows,
            )
            connection.executemany(
                "INSERT INTO reading (session_id, source_id, seq, measurand_id, offset_ns, value)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                reading_rows,
            )

    def write_tick(self, tick: Tick) -> None:
        self._open()
        if tick.loop not in self._loops:
            raise NotDeclaredError("loop", tick.loop)
        with self._store._transaction() as connection:
            connection.execute(
                "INSERT INTO tick (session_id, loop, offset_ns, mode, reading, setpoint,"
                " correction, demand, expected, delivered_correction)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._session.id,
                    tick.loop,
                    tick.offset_ns,
                    tick.mode,
                    tick.reading,
                    tick.setpoint,
                    tick.correction,
                    tick.demand,
                    tick.expected,
                    tick.delivered_correction,
                ),
            )

    def write_event(self, event: Event) -> int:
        self._open()
        with self._store._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO event (session_id, offset_ns, source, kind, detail)"
                " VALUES (?, ?, ?, ?, ?)",
                (self._session.id, event.offset_ns, event.source, event.kind, _dumps(event.detail)),
            )
            return int(cursor.lastrowid or 0)

    def open_span(
        self,
        kind: SpanKind,
        label: str,
        start_ns: int,
        parent_id: int | None = None,
        details: Any = None,
    ) -> int:
        self._open()
        with self._store._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO span (session_id, parent_id, kind, label, start_ns, details)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (self._session.id, parent_id, kind.value, label, start_ns, _dumps(details)),
            )
            return int(cursor.lastrowid or 0)

    def close_span(self, span_id: int, end_ns: int, details: Any = None) -> None:
        self._open()
        with self._store._transaction() as connection:
            if details is None:
                connection.execute("UPDATE span SET end_ns = ? WHERE id = ?", (end_ns, span_id))
            else:
                connection.execute(
                    "UPDATE span SET end_ns = ?, details = ? WHERE id = ?",
                    (end_ns, _dumps(details), span_id),
                )

    # endregion

    def end(self, end_ns: int) -> None:
        self._open()
        with self._store._transaction() as connection:
            connection.execute(
                "UPDATE session SET end_ns = ? WHERE id = ?", (end_ns, self._session.id)
            )
        self._ended = True


class SqliteStore:
    """A :class:`Store` on one SQLite file. ``":memory:"`` for tests."""

    __slots__ = ("_connection", "_lock", "path")

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path) if path != ":memory:" else path
        self._lock = RLock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA synchronous = NORMAL")
        migrate(self._connection)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN")
            try:
                yield self._connection
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            self._connection.execute("COMMIT")

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(sql, tuple(params)).fetchall()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    # region Sessions

    def open_session(
        self,
        start_ns: int,
        version: str | None = None,
        config: Any = None,
        hardware: Any = None,
        details: Any = None,
    ) -> SqliteSessionWriter:
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO session (start_ns, version, config, hardware, details)"
                " VALUES (?, ?, ?, ?, ?)",
                (start_ns, version, _dumps(config), _dumps(hardware), _dumps(details)),
            )
            session_id = int(cursor.lastrowid or 0)
        return SqliteSessionWriter(self, self.session(session_id))

    def sessions(self, limit: int | None = None) -> list[SessionRow]:
        sql = "SELECT * FROM session ORDER BY start_ns DESC"
        rows = self._query(sql + " LIMIT ?", (limit,)) if limit is not None else self._query(sql)
        return [_session_row(r) for r in rows]

    def session(self, session_id: int) -> SessionRow:
        rows = self._query("SELECT * FROM session WHERE id = ?", (session_id,))
        if not rows:
            raise SessionNotFoundError(session_id)
        return _session_row(rows[0])

    def delete_session(self, session_id: int) -> None:
        with self._transaction() as connection:
            if connection.execute("DELETE FROM session WHERE id = ?", (session_id,)).rowcount == 0:
                raise SessionNotFoundError(session_id)

    # endregion

    # region What a session recorded

    def _measurands(self, session_id: int) -> dict[int, MeasurandRow]:
        return {
            r["id"]: MeasurandRow(r["id"], r["name"], r["unit"], r["label"])
            for r in self._query("SELECT * FROM measurand WHERE session_id = ?", (session_id,))
        }

    def sources(self, session_id: int) -> list[SourceRow]:
        return [
            SourceRow(r["id"], r["name"], r["kind"])
            for r in self._query(
                "SELECT * FROM source WHERE session_id = ? ORDER BY id", (session_id,)
            )
        ]

    def channels(self, session_id: int) -> list[ChannelRow]:
        measurands = self._measurands(session_id)
        sources = {s.id: s for s in self.sources(session_id)}
        return [
            ChannelRow(sources[r["source_id"]], measurands[r["measurand_id"]])
            for r in self._query(
                "SELECT source_id, measurand_id FROM channel WHERE session_id = ?"
                " ORDER BY source_id, measurand_id",
                (session_id,),
            )
        ]

    def _channel(self, session_id: int, source: str, measurand: str) -> ChannelRow:
        rows = self._query(
            "SELECT s.id AS sid, s.name AS sname, s.kind, q.id AS qid, q.name AS qname,"
            " q.unit, q.label FROM channel c"
            " JOIN source s ON s.session_id = c.session_id AND s.id = c.source_id"
            " JOIN measurand q ON q.session_id = c.session_id AND q.id = c.measurand_id"
            " WHERE c.session_id = ? AND s.name = ? AND q.name = ?",
            (session_id, source, measurand),
        )
        if not rows:
            raise NotDeclaredError("channel", f"{source}.{measurand}")
        r = rows[0]
        return ChannelRow(
            SourceRow(r["sid"], r["sname"], r["kind"]),
            MeasurandRow(r["qid"], r["qname"], r["unit"], r["label"]),
        )

    def actuators(self, session_id: int) -> list[ActuatorRow]:
        return [
            ActuatorRow(r["name"], r["kind"], _loads(r["config"]))
            for r in self._query(
                "SELECT * FROM actuator WHERE session_id = ? ORDER BY name", (session_id,)
            )
        ]

    def loops(self, session_id: int) -> list[LoopRow]:
        measurands = self._measurands(session_id)
        sources = {s.id: s for s in self.sources(session_id)}
        actuators = {a.name: a for a in self.actuators(session_id)}
        return [
            LoopRow(
                actuators[r["name"]],
                ChannelRow(sources[r["source_id"]], measurands[r["measurand_id"]]),
                _loads(r["config"]),
            )
            for r in self._query(
                "SELECT * FROM loop WHERE session_id = ? ORDER BY name", (session_id,)
            )
        ]

    def series(
        self,
        session_id: int,
        source: str,
        measurand: str,
        window: Window | None = None,
        downsample: Downsample | None = None,
    ) -> Series:
        channel = self._channel(session_id, source, measurand)
        where, params = _window_clause(window, "offset_ns")
        # Every form below is one range scan of reading_by_channel: the index
        # carries offset_ns and value, so neither the table nor sample is read.
        base = " FROM reading WHERE session_id = ? AND source_id = ? AND measurand_id = ?" + where
        key = [session_id, channel.source.id, channel.measurand.id, *params]

        match downsample:
            case None:
                rows = self._query(
                    "SELECT offset_ns AS t, value AS v" + base + " ORDER BY offset_ns", key
                )
            case Downsample(every=int(n)):
                rows = self._query(
                    "SELECT offset_ns AS t, value AS v"
                    + base
                    + " AND seq % ? = 0 ORDER BY offset_ns",
                    [*key, n],
                )
            case Downsample(max_points=int(max_points)):
                bounds = self._query(
                    "SELECT MIN(offset_ns) AS lo, MAX(offset_ns) AS hi" + base, key
                )
                lo, hi = bounds[0]["lo"], bounds[0]["hi"]
                if lo is None:
                    return Series(channel, (), downsample)
                bucket_ns = max(1, (hi - lo) // max_points + 1)
                return self.series(
                    session_id, source, measurand, window, Downsample(bucket_ns=bucket_ns)
                )
            case Downsample(bucket_ns=int(bucket_ns)):
                rows = self._query(
                    "SELECT (offset_ns / ?) * ? AS t, AVG(value) AS v"
                    + base
                    + " GROUP BY offset_ns / ? ORDER BY t",
                    [bucket_ns, bucket_ns, *key, bucket_ns],
                )
        return Series(channel, tuple(Point(r["t"], r["v"]) for r in rows), downsample)

    def ticks(self, session_id: int, loop: str, window: Window | None = None) -> list[Tick]:
        where, params = _window_clause(window, "offset_ns")
        return [
            Tick(
                loop=r["loop"],
                offset_ns=r["offset_ns"],
                mode=r["mode"],
                correction=r["correction"],
                reading=r["reading"],
                setpoint=r["setpoint"],
                demand=r["demand"],
                expected=r["expected"],
                delivered_correction=r["delivered_correction"],
            )
            for r in self._query(
                "SELECT * FROM tick WHERE session_id = ? AND loop = ?"
                + where
                + " ORDER BY offset_ns",
                [session_id, loop, *params],
            )
        ]

    def events(
        self, session_id: int, window: Window | None = None, kind: str | None = None
    ) -> list[Event]:
        where, params = _window_clause(window, "offset_ns")
        if kind is not None:
            where += " AND kind = ?"
            params.append(kind)  # type: ignore[arg-type]
        return [
            Event(r["offset_ns"], r["kind"], r["source"], _loads(r["detail"]), r["id"])
            for r in self._query(
                "SELECT * FROM event WHERE session_id = ?" + where + " ORDER BY offset_ns, id",
                [session_id, *params],
            )
        ]

    def spans(self, session_id: int) -> list[Span]:
        return [
            Span(
                r["id"],
                SpanKind(r["kind"]),
                r["label"],
                r["start_ns"],
                r["end_ns"],
                r["parent_id"],
                _loads(r["details"]),
            )
            for r in self._query(
                "SELECT * FROM span WHERE session_id = ? ORDER BY start_ns, id", (session_id,)
            )
        ]

    # endregion

    # region Tunings

    def save_tuning(
        self,
        name: str,
        law: str,
        config: dict[str, Any],
        created_ns: int,
        session_id: int | None = None,
        loop: str | None = None,
        notes: Any = None,
    ) -> TuningRow:
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO tuning (name, law, config, created_ns, session_id, loop, notes)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, law, _dumps(config), created_ns, session_id, loop, _dumps(notes)),
            )
            tuning_id = int(cursor.lastrowid or 0)
        return _tuning_row(self._query("SELECT * FROM tuning WHERE id = ?", (tuning_id,))[0])

    def tuning(self, name: str) -> TuningRow:
        rows = self._query(
            "SELECT * FROM tuning WHERE name = ? ORDER BY created_ns DESC, id DESC LIMIT 1", (name,)
        )
        if not rows:
            raise TuningNotFoundError(name)
        return _tuning_row(rows[0])

    def tunings(self) -> list[TuningRow]:
        return [
            _tuning_row(r)
            for r in self._query(
                "SELECT t.* FROM tuning t JOIN ("
                " SELECT name, MAX(created_ns) AS created_ns FROM tuning GROUP BY name"
                ") newest ON newest.name = t.name AND newest.created_ns = t.created_ns"
                " ORDER BY t.name"
            )
        ]

    def tuning_history(self, name: str) -> list[TuningRow]:
        return [
            _tuning_row(r)
            for r in self._query(
                "SELECT * FROM tuning WHERE name = ? ORDER BY created_ns DESC, id DESC", (name,)
            )
        ]

    def delete_tuning(self, name: str) -> None:
        with self._transaction() as connection:
            if connection.execute("DELETE FROM tuning WHERE name = ?", (name,)).rowcount == 0:
                raise TuningNotFoundError(name)

    # endregion
