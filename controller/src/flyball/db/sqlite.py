"""The SQLite store.

One locked connection per [SqliteStore][flyball.db.sqlite.SqliteStore]; the
rig's writer and the server's reader normally each open their own on the same
file, and WAL lets them overlap. Declarations are interned in the writer so a
delivery is one `executemany` per table.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any

from pydantic_core import to_jsonable_python

from flyball.core.device import Device
from flyball.core.errors import ConflictError, NotFoundError
from flyball.core.signal import Access, Band, Limit, Sample, Signal, WriteState

from .errors import (
    DashboardNotFoundError,
    NotDeclaredError,
    ProgramNotFoundError,
    SessionEndedError,
    SessionNotFoundError,
    TuningNotFoundError,
)
from .migrate import migrate
from .types import (
    ControllerRow,
    DashboardRow,
    DeviceRow,
    Downsample,
    Event,
    Point,
    ProgramFormat,
    ProgramRow,
    RigVersionRow,
    SampleRow,
    Series,
    SessionRow,
    SignalRow,
    Span,
    SpanKind,
    Tick,
    TuningRow,
    Window,
    WriteRow,
    WriteStateRow,
)

if TYPE_CHECKING:
    from flyball.control import Controller

# region Helpers


def _dumps(value: Any) -> str | None:
    return None if value is None else json.dumps(value, separators=(",", ":"))


def _loads(text: str | None) -> Any:
    return None if text is None else json.loads(text)


def _encode_reading(dtype: str, value: Any) -> Any:
    """A reading's value as the `reading.value` column takes it: floats as themselves.

    Everything else is JSON text: an enum's `.value`, bool/int/str dumped as
    they are, a `json` value through `to_jsonable_python` first.
    """
    if dtype == "float":
        return value
    if dtype == "enum":
        value = value.value
    elif dtype == "json":
        value = to_jsonable_python(value)
    return json.dumps(value)


def _decode_reading(dtype: str, raw: Any) -> Any:
    """The column's value back to a reading: undoes `_encode_reading`.

    SQLite's REAL affinity turns numeric-looking JSON text (an int, a bare
    digit) into a number on the way in, so a non-float value may already be
    numeric here rather than the str `_encode_reading` wrote.
    """
    if dtype == "float":
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    if dtype == "int":
        return int(raw)
    if dtype == "bool":
        return bool(raw)
    return raw


def _band(text: str | None) -> Band | None:
    if text is None:
        return None
    lo, hi = json.loads(text)
    return (lo, hi)


def _config_json(device: Device) -> Any:
    """The driver config as JSON; a built link inside it is named, not serialised."""
    config = device.config
    return config.model_dump(
        mode="json", fallback=lambda value: getattr(value, "name", type(value).__name__)
    )


def _device_row(row: sqlite3.Row) -> DeviceRow:
    return DeviceRow(row["id"], row["address"], row["driver"], _loads(row["config"]), row["label"])


def _signal_row(row: sqlite3.Row) -> SignalRow:
    return SignalRow(
        id=row["id"],
        device_id=row["device_id"],
        address=row["address"],
        quantity=row["quantity"],
        unit=row["unit"],
        access=row["access"],
        dtype=row["dtype"],
        shape=_loads(row["shape"]),
        label=row["label"],
        range=_band(row["range"]),
        precision=row["precision"],
        warn=_band(row["warn"]),
        alarm=_band(row["alarm"]),
        limits=_band(row["limits"]),
    )


def _window_clause(window: Window | None, column: str) -> tuple[str, list[int]]:
    """SQL and parameters restricting `column` to the window; empty when unbounded."""
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


def _dashboard_row(row: sqlite3.Row) -> DashboardRow:
    return DashboardRow(
        id=row["id"],
        name=row["name"],
        rig=row["rig"],
        body=_loads(row["body"]),
        created_ns=row["created_ns"],
        sha256=row["sha256"],
    )


def _program_row(row: sqlite3.Row) -> ProgramRow:
    return ProgramRow(
        id=row["id"],
        name=row["name"],
        format=row["format"],
        body=row["body"],
        created_ns=row["created_ns"],
        sha256=row["sha256"],
        label=row["label"],
        notes=_loads(row["notes"]),
    )


def _tuning_row(row: sqlite3.Row) -> TuningRow:
    return TuningRow(
        id=row["id"],
        name=row["name"],
        law=row["law"],
        config=_loads(row["config"]),
        created_ns=row["created_ns"],
        session_id=row["session_id"],
        controller=row["controller"],
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
        rig_version_id=row["rig_version_id"],
    )


def _rig_version_row(row: sqlite3.Row) -> RigVersionRow:
    return RigVersionRow(
        id=row["id"],
        time_ns=row["time_ns"],
        reason=row["reason"],
        files=_loads(row["files"]) or [],
        document=_loads(row["document"]),
    )


# endregion


class SqliteSessionWriter:
    """Appends to one session. Not thread-safe on its own; the store's lock covers it."""

    __slots__ = (
        "_controllers",
        "_devices",
        "_ended",
        "_seq",
        "_session",
        "_signals",
        "_store",
        "_writes",
    )

    def __init__(self, store: SqliteStore, session: SessionRow) -> None:
        self._store = store
        self._session = session
        self._devices: dict[Device, int] = {}
        self._signals: dict[Signal, int] = {}
        self._writes: set[Signal] = set()
        self._controllers: set[str] = set()
        self._seq: dict[int, int] = {}  # the last seq written, per device id
        self._ended = False

    @property
    def session(self) -> SessionRow:
        return self._session

    def _open(self) -> None:
        if self._ended:
            raise SessionEndedError(self._session.id)

    # region Declarations

    def declare_device(self, device: Device) -> None:
        self._open()
        if device in self._devices:
            return
        config = device.config
        with self._store._transaction() as connection:
            did = len(self._devices) + 1
            connection.execute(
                "INSERT INTO device (session_id, id, address, driver, config, label)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self._session.id,
                    did,
                    device.name,
                    config.config_tag or type(device).__name__,
                    _dumps(_config_json(device)),
                    device.label,
                ),
            )
            self._devices[device] = did

    def declare_signal(self, signal: Signal) -> None:
        self._open()
        if signal in self._signals:
            return
        if (did := self._devices.get(signal.device)) is None:
            raise NotDeclaredError("device", signal.device.name)
        spec = signal.spec
        with self._store._transaction() as connection:
            sid = len(self._signals) + 1
            connection.execute(
                "INSERT INTO signal (session_id, id, device_id, address, quantity, unit, access,"
                " dtype, shape, label, range, precision, warn, alarm, limits)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._session.id,
                    sid,
                    did,
                    signal.address,
                    spec.quantity.name,
                    spec.quantity.unit.symbol,
                    str(signal.access),
                    spec.dtype,
                    _dumps(list(spec.shape)),
                    spec.label or None,
                    _dumps(spec.range),
                    spec.precision,
                    _dumps(spec.warn),
                    _dumps(spec.alarm),
                    _dumps(signal.limits),  # effective: a limit that follows a signal, as a number
                ),
            )
            if Access.W in signal.access:
                connection.execute(
                    "INSERT INTO write (session_id, signal_id, driver, limits) VALUES (?, ?, ?, ?)",
                    (
                        self._session.id,
                        sid,
                        signal.device.config.config_tag or type(signal.device).__name__,
                        _dumps(signal.limits),
                    ),
                )
                self._writes.add(signal)
            self._signals[signal] = sid

    def declare_controller(self, controller: Controller) -> None:
        self._open()
        name = controller.name
        if name in self._controllers:
            return
        if controller.target not in self._writes:
            raise NotDeclaredError("write", controller.target.address)
        if controller.source not in self._signals:
            raise NotDeclaredError("signal", controller.source.address)
        law = None if controller.law is None else controller.law.config.model_dump(mode="json")
        feedforward = controller.feedforward.config.model_dump(mode="json")
        with self._store._transaction() as connection:
            connection.execute(
                "INSERT INTO controller (session_id, name, source, law, feedforward)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    self._session.id,
                    name,
                    controller.source.address,
                    _dumps(law),
                    _dumps(feedforward),
                ),
            )
            self._controllers.add(name)

    # endregion

    # region Data

    def write_samples(self, samples: Iterable[Sample]) -> None:
        self._open()
        session_id = self._session.id
        sample_rows: list[tuple[int, int, int, str, int]] = []
        reading_rows: list[tuple[int, int, int, int, int, Any]] = []
        seqs = dict(self._seq)
        for sample in samples:
            node = sample.node
            if (did := self._devices.get(node.device)) is None:
                raise NotDeclaredError("device", node.device.name)
            offset = sample.time_ns - self._session.start_ns
            seq = seqs[did] = seqs.get(did, 0) + 1
            sample_rows.append((session_id, did, seq, node.address, offset))
            for signal, value in sample.values.items():
                if (sid := self._signals.get(signal)) is None:
                    raise NotDeclaredError("signal", signal.address)
                dtype = signal.spec.dtype
                if dtype == "float":
                    if value == value:  # NaN is a fault, not a reading; the writer routes those
                        reading_rows.append((session_id, did, seq, sid, offset, value))
                else:
                    reading_rows.append((
                        session_id,
                        did,
                        seq,
                        sid,
                        offset,
                        _encode_reading(dtype, value),
                    ))
        if not sample_rows:
            return
        with self._store._transaction() as connection:
            connection.executemany(
                "INSERT INTO sample (session_id, device_id, seq, node, offset_ns)"
                " VALUES (?, ?, ?, ?, ?)",
                sample_rows,
            )
            connection.executemany(
                "INSERT INTO reading (session_id, device_id, seq, signal_id, offset_ns, value)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                reading_rows,
            )
        self._seq = seqs

    def write_states(self, offset_ns: int, states: Mapping[Signal, WriteState]) -> None:
        self._open()
        rows = []
        for signal, state in states.items():
            if signal not in self._writes:
                raise NotDeclaredError("write", signal.address)
            rows.append((
                self._session.id,
                self._signals[signal],
                offset_ns,
                state.value,
                state.requested,
                state.at_limit,
                state.controller,
            ))
        if not rows:
            return
        with self._store._transaction() as connection:
            # Two commits at one instant -- a manual demand and the delivery
            # that follows on a coarse clock -- keep the later state.
            connection.executemany(
                "INSERT OR REPLACE INTO write_state"
                " (session_id, signal_id, offset_ns, value, requested, at_limit, controller)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def write_tick(self, tick: Tick) -> None:
        self.write_ticks((tick,))

    def write_ticks(self, ticks: Iterable[Tick]) -> None:
        self._open()
        rows = []
        for tick in ticks:
            if tick.controller not in self._controllers:
                raise NotDeclaredError("controller", tick.controller)
            rows.append((
                self._session.id,
                tick.controller,
                tick.offset_ns,
                tick.mode,
                tick.reading,
                tick.setpoint,
                tick.correction,
                tick.demand,
                tick.expected,
                tick.delivered_correction,
            ))
        if not rows:
            return
        with self._store._transaction() as connection:
            connection.executemany(
                "INSERT INTO tick (session_id, controller, offset_ns, mode, reading, setpoint,"
                " correction, demand, expected, delivered_correction)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
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
    """A [Store][flyball.db.store.Store] on one SQLite file. `":memory:"` for tests."""

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
        rig_version_id: int | None = None,
    ) -> SqliteSessionWriter:
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO session (start_ns, version, config, hardware, details,"
                " rig_version_id) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    start_ns,
                    version,
                    _dumps(config),
                    _dumps(hardware),
                    _dumps(details),
                    rig_version_id,
                ),
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

    def end_session(self, session_id: int, end_ns: int | None = None) -> SessionRow:
        session = self.session(session_id)
        if session.end_ns is not None:
            raise SessionEndedError(session_id)
        if end_ns is None:
            last = self._query(
                "SELECT MAX(offset_ns) AS last FROM sample WHERE session_id = ?", (session_id,)
            )[0]["last"]
            end_ns = session.start_ns + (last or 0)
        with self._transaction() as connection:
            connection.execute("UPDATE session SET end_ns = ? WHERE id = ?", (end_ns, session_id))
        return self.session(session_id)

    def delete_session(self, session_id: int) -> None:
        with self._transaction() as connection:
            if connection.execute("DELETE FROM session WHERE id = ?", (session_id,)).rowcount == 0:
                raise SessionNotFoundError(session_id)

    # endregion

    # region What a session recorded

    def devices(self, session_id: int) -> list[DeviceRow]:
        return [
            _device_row(r)
            for r in self._query(
                "SELECT * FROM device WHERE session_id = ? ORDER BY id", (session_id,)
            )
        ]

    def signals(self, session_id: int) -> list[SignalRow]:
        return [
            _signal_row(r)
            for r in self._query(
                "SELECT * FROM signal WHERE session_id = ? ORDER BY device_id, id", (session_id,)
            )
        ]

    def _signal(self, session_id: int, address: str) -> SignalRow:
        rows = self._query(
            "SELECT * FROM signal WHERE session_id = ? AND address = ?", (session_id, address)
        )
        if not rows:
            raise NotDeclaredError("signal", address)
        return _signal_row(rows[0])

    def writes(self, session_id: int) -> list[WriteRow]:
        signals = {s.id: s for s in self.signals(session_id)}
        return [
            WriteRow(signals[r["signal_id"]], r["driver"], _band(r["limits"]))
            for r in self._query(
                "SELECT * FROM write WHERE session_id = ? ORDER BY signal_id", (session_id,)
            )
        ]

    def controllers(self, session_id: int) -> list[ControllerRow]:
        return [
            ControllerRow(r["name"], r["source"], _loads(r["law"]), _loads(r["feedforward"]))
            for r in self._query(
                "SELECT * FROM controller WHERE session_id = ? ORDER BY name", (session_id,)
            )
        ]

    def series(
        self,
        session_id: int,
        address: str,
        window: Window | None = None,
        downsample: Downsample | None = None,
    ) -> Series:
        signal = self._signal(session_id, address)
        where, params = _window_clause(window, "offset_ns")
        # Every form below is one range scan of reading_by_signal: the index
        # carries offset_ns and value, so neither the table nor sample is read.
        base = " FROM reading WHERE session_id = ? AND signal_id = ?" + where
        key = [session_id, signal.id, *params]

        if signal.dtype != "float":
            if downsample is not None:
                raise ValueError(
                    f"signal {address!r} is {signal.dtype!r}: downsample only applies to float"
                    " signals"
                )
            rows = self._query(
                "SELECT offset_ns AS t, value AS v" + base + " ORDER BY offset_ns", key
            )
            points = tuple(Point(r["t"], _decode_reading(signal.dtype, r["v"])) for r in rows)
            return Series(signal, points, None)

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
                    return Series(signal, (), downsample)
                bucket_ns = max(1, (hi - lo) // max_points + 1)
                return self.series(session_id, address, window, Downsample(bucket_ns=bucket_ns))
            case Downsample(bucket_ns=int(bucket_ns)):
                rows = self._query(
                    "SELECT (offset_ns / ?) * ? AS t, AVG(value) AS v"
                    + base
                    + " GROUP BY offset_ns / ? ORDER BY t",
                    [bucket_ns, bucket_ns, *key, bucket_ns],
                )
            case _:
                raise ValueError(f"{downsample!r} names none of every, bucket_ns or max_points")
        return Series(signal, tuple(Point(r["t"], r["v"]) for r in rows), downsample)

    def samples(
        self, session_id: int, address: str, window: Window | None = None
    ) -> list[SampleRow]:
        name = address.partition(".")[0]
        device = next((d for d in self.devices(session_id) if d.address == name), None)
        if device is None:
            raise NotFoundError(f"Device {name!r} not in session {session_id}")
        signals = {s.id: s for s in self.signals(session_id)}
        where, params = _window_clause(window, "s.offset_ns")
        # A namespace's address selects the samples on it and under it.
        under = "" if address == name else " AND (node = ? OR node LIKE ?)"
        rows = self._query(
            "SELECT s.seq, s.node, r.signal_id, r.offset_ns, r.value FROM sample s"
            " JOIN reading r ON r.session_id = s.session_id AND r.device_id = s.device_id"
            " AND r.seq = s.seq WHERE s.session_id = ? AND s.device_id = ?"
            + under
            + where
            + " ORDER BY s.seq",
            [session_id, device.id, *([address, address + ".%"] if under else []), *params],
        )
        samples: dict[int, SampleRow] = {}
        for r in rows:
            row = samples.get(r["seq"])
            if row is None:
                row = samples[r["seq"]] = SampleRow(r["seq"], r["offset_ns"], r["node"], {})
            signal = signals[r["signal_id"]]
            row.values[signal.address] = _decode_reading(signal.dtype, r["value"])
        return list(samples.values())

    def write_states(
        self, session_id: int, address: str, window: Window | None = None
    ) -> list[WriteStateRow]:
        signal = self._signal(session_id, address)
        where, params = _window_clause(window, "offset_ns")
        return [
            WriteStateRow(
                r["offset_ns"],
                r["value"],
                r["requested"],
                None if r["at_limit"] is None else Limit(r["at_limit"]),
                r["controller"],
            )
            for r in self._query(
                "SELECT * FROM write_state WHERE session_id = ? AND signal_id = ?"
                + where
                + " ORDER BY offset_ns",
                [session_id, signal.id, *params],
            )
        ]

    def ticks(
        self,
        session_id: int,
        controller: str,
        window: Window | None = None,
        every: int | None = None,
    ) -> list[Tick]:
        where, params = _window_clause(window, "offset_ns")
        # Ticks have no sequence number of their own: number them in order and keep every nth.
        thin = "" if every is None or every <= 1 else f" AND (rn - 1) % {int(every)} = 0"
        return [
            Tick(
                controller=r["controller"],
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
                "SELECT * FROM (SELECT *, ROW_NUMBER() OVER (ORDER BY offset_ns) AS rn"
                " FROM tick WHERE session_id = ? AND controller = ?"
                + where
                + ") WHERE 1 = 1"
                + thin
                + " ORDER BY offset_ns",
                [session_id, controller, *params],
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

    # region Rig versions

    def save_rig_version(
        self, time_ns: int, reason: str, document: dict[str, Any], files: Sequence[str] = ()
    ) -> RigVersionRow:
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO rig_version (time_ns, reason, files, document) VALUES (?, ?, ?, ?)",
                (time_ns, reason, _dumps(list(files)), _dumps(document)),
            )
            version_id = int(cursor.lastrowid or 0)
        return self.rig_version(version_id)

    def rig_versions(self, limit: int | None = None) -> list[RigVersionRow]:
        rows = self._query(
            "SELECT * FROM rig_version ORDER BY id DESC" + ("" if limit is None else " LIMIT ?"),
            () if limit is None else (limit,),
        )
        return [_rig_version_row(r) for r in rows]

    def rig_version(self, version_id: int) -> RigVersionRow:
        rows = self._query("SELECT * FROM rig_version WHERE id = ?", (version_id,))
        if not rows:
            raise NotFoundError(f"Rig version {version_id} not found")
        return _rig_version_row(rows[0])

    def latest_rig_version(self) -> RigVersionRow | None:
        rows = self._query("SELECT * FROM rig_version ORDER BY id DESC LIMIT 1")
        return _rig_version_row(rows[0]) if rows else None

    # endregion

    def save_tuning(
        self,
        name: str,
        law: str,
        config: dict[str, Any],
        created_ns: int,
        session_id: int | None = None,
        controller: str | None = None,
        notes: Any = None,
    ) -> TuningRow:
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO tuning (name, law, config, created_ns, session_id, controller, notes)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, law, _dumps(config), created_ns, session_id, controller, _dumps(notes)),
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

    # region Programs

    def save_program(
        self,
        name: str,
        format: ProgramFormat,
        body: str,
        created_ns: int,
        label: str | None = None,
        notes: Any = None,
    ) -> ProgramRow:
        digest = hashlib.sha256(body.encode()).hexdigest()
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO program (name, format, body, created_ns, label, notes, sha256)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, format, body, created_ns, label, _dumps(notes), digest),
            )
            program_id = int(cursor.lastrowid or 0)
        return self.program_version(program_id)

    def program(self, name: str) -> ProgramRow:
        rows = self._query(
            "SELECT * FROM program WHERE name = ? ORDER BY created_ns DESC, id DESC LIMIT 1",
            (name,),
        )
        if not rows:
            raise ProgramNotFoundError(name)
        return _program_row(rows[0])

    def program_version(self, program_id: int) -> ProgramRow:
        rows = self._query("SELECT * FROM program WHERE id = ?", (program_id,))
        if not rows:
            raise ProgramNotFoundError(f"#{program_id}")
        return _program_row(rows[0])

    def programs(self) -> list[ProgramRow]:
        return [
            _program_row(r)
            for r in self._query(
                "SELECT p.* FROM program p JOIN ("
                " SELECT name, MAX(id) AS id FROM program GROUP BY name"
                ") newest ON newest.id = p.id ORDER BY p.name"
            )
        ]

    def program_history(self, name: str) -> list[ProgramRow]:
        return [
            _program_row(r)
            for r in self._query(
                "SELECT * FROM program WHERE name = ? ORDER BY created_ns DESC, id DESC", (name,)
            )
        ]

    def delete_program(self, name: str) -> None:
        with self._transaction() as connection:
            if connection.execute("DELETE FROM program WHERE name = ?", (name,)).rowcount == 0:
                raise ProgramNotFoundError(name)

    def rename_program(self, name: str, new_name: str) -> list[ProgramRow]:
        if name == new_name:
            return self.program_history(name)
        with self._transaction() as connection:
            taken = connection.execute(
                "SELECT 1 FROM program WHERE name = ? LIMIT 1", (new_name,)
            ).fetchone()
            if taken is not None:
                raise ConflictError(f"a program named {new_name!r} already exists")
            moved = connection.execute(
                "UPDATE program SET name = ? WHERE name = ?", (new_name, name)
            ).rowcount
            if moved == 0:
                raise ProgramNotFoundError(name)
        return self.program_history(new_name)

    # endregion

    # region Dashboards

    def save_dashboard(self, name: str, rig: str, body: Any, created_ns: int) -> DashboardRow:
        text = json.dumps(body, separators=(",", ":"))  # a null body is still a document
        digest = hashlib.sha256(text.encode()).hexdigest()
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO dashboard (name, rig, body, created_ns, sha256)"
                " VALUES (?, ?, ?, ?, ?)",
                (name, rig, text, created_ns, digest),
            )
            return self.dashboard_version(cursor.lastrowid)  # type: ignore[arg-type]

    def dashboard(self, name: str) -> DashboardRow:
        rows = self._query(
            "SELECT * FROM dashboard WHERE name = ? ORDER BY created_ns DESC, id DESC LIMIT 1",
            (name,),
        )
        if not rows:
            raise DashboardNotFoundError(name)
        return _dashboard_row(rows[0])

    def dashboard_version(self, dashboard_id: int) -> DashboardRow:
        rows = self._query("SELECT * FROM dashboard WHERE id = ?", (dashboard_id,))
        if not rows:
            raise DashboardNotFoundError(f"#{dashboard_id}")
        return _dashboard_row(rows[0])

    def dashboards(self, rig: str | None = None) -> list[DashboardRow]:
        newest = (
            "SELECT d.* FROM dashboard d JOIN ("
            " SELECT name, MAX(id) AS id FROM dashboard GROUP BY name"
            ") newest ON newest.id = d.id"
        )
        rows = (
            self._query(newest + " ORDER BY d.name")
            if rig is None
            else self._query(newest + " WHERE d.rig = ? ORDER BY d.name", (rig,))
        )
        return [_dashboard_row(r) for r in rows]

    def dashboard_history(self, name: str) -> list[DashboardRow]:
        return [
            _dashboard_row(r)
            for r in self._query(
                "SELECT * FROM dashboard WHERE name = ? ORDER BY created_ns DESC, id DESC", (name,)
            )
        ]

    def delete_dashboard(self, name: str) -> None:
        with self._transaction() as connection:
            if connection.execute("DELETE FROM dashboard WHERE name = ?", (name,)).rowcount == 0:
                raise DashboardNotFoundError(name)

    def rename_dashboard(self, name: str, new_name: str) -> list[DashboardRow]:
        if name == new_name:
            return self.dashboard_history(name)
        with self._transaction() as connection:
            taken = connection.execute(
                "SELECT 1 FROM dashboard WHERE name = ? LIMIT 1", (new_name,)
            ).fetchone()
            if taken is not None:
                raise ConflictError(f"a dashboard named {new_name!r} already exists")
            moved = connection.execute(
                "UPDATE dashboard SET name = ? WHERE name = ?", (new_name, name)
            ).rowcount
            if moved == 0:
                raise DashboardNotFoundError(name)
        return self.dashboard_history(new_name)

    # endregion
