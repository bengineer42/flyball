"""Downloads: a session, one signal, one controller or the events as a file.

Every export is a plain table with the same two leading time columns --
``time_s`` (seconds since the session started) and ``time`` (ISO 8601, UTC) --
so files from one session line up in a spreadsheet. ``format`` is ``csv`` or
``json``; a whole session can also come as a ``zip`` of every table plus the
session's metadata.

A ``wide`` session table has one column per signal (headed by its address and
unit) and one row per instant (or per ``step_s``), each signal holding its
last value; ``long`` has one row per raw value (``device, signal, unit,
value``).
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response

from flyball.interfaces.server.deps import StoreDep
from flyball.record import SignalRow, Store

router = APIRouter(prefix="/api/history/sessions/{session_id}", tags=["history"])

Format = Literal["csv", "json"]
Layout = Literal["wide", "long"]

TIME_COLUMNS = ("time_s", "time")

_ABSENT = object()
"""Not in this sample, as against in it with no value (None)."""


def _stamp(start_ns: int, offset_ns: int) -> tuple[float, str]:
    at = datetime.fromtimestamp((start_ns + offset_ns) / 1e9, tz=UTC)
    return offset_ns / 1e9, at.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _table(
    columns: Sequence[str], rows: Iterable[Sequence[Any]], format: Format
) -> tuple[bytes, str]:
    """Rows into bytes and a media type; JSON is a list of objects keyed by column."""
    if format == "json":
        body = json.dumps([dict(zip(columns, row, strict=True)) for row in rows]).encode()
        return body, "application/json"
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return out.getvalue().encode(), "text/csv; charset=utf-8"


def _attachment(body: bytes, media: str, name: str) -> Response:
    return Response(
        body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


def _column(signal: SignalRow) -> str:
    return f"{signal.address} ({signal.unit})"


# region Tables


def session_table(
    store: Store, session_id: int, layout: Layout, step_s: float | None = None
) -> tuple[list[str], list[list]]:
    session = store.session(session_id)
    signals = store.signals(session_id)
    if layout == "long":
        rows: list[list] = []
        units = {s.address: s.unit for s in signals}
        for device in store.devices(session_id):
            for sample in store.samples(session_id, device.address):
                stamp = _stamp(session.start_ns, sample.offset_ns)
                for address, value in sample.values.items():
                    rows.append([*stamp, device.address, address, units.get(address, ""), value])
        rows.sort(key=lambda r: r[0])
        return [*TIME_COLUMNS, "device", "signal", "unit", "value"], rows

    order = {s.address: i for i, s in enumerate(signals)}
    by_instant: dict[int, list] = {}
    for device in store.devices(session_id):
        for sample in store.samples(session_id, device.address):
            row = by_instant.setdefault(sample.offset_ns, [_ABSENT] * len(signals))
            for address, value in sample.values.items():
                index = order.get(address)
                if index is not None:
                    row[index] = value
    # Devices sample on their own clocks, milliseconds apart, so a row per raw
    # instant would be mostly blank: each row carries the last value of every
    # signal instead, and `step_s` resamples that onto a regular grid. A reading
    # with no value (stored null) is carried as a blank until a value comes; on
    # the grid, a cell is blank if any reading since the last cell had no value.
    held: list = [None] * len(signals)
    rows: list[list] = []
    instants = sorted(by_instant)
    if step_s is None:
        for at in instants:
            held = [h if v is _ABSENT else v for v, h in zip(by_instant[at], held, strict=True)]
            rows.append([*_stamp(session.start_ns, at), *held])
    elif instants:
        step_ns = int(step_s * 1e9)
        i = 0
        for at in range(0, instants[-1] + 1, step_ns):
            broken = [False] * len(signals)
            while i < len(instants) and instants[i] <= at:
                values = by_instant[instants[i]]
                held = [h if v is _ABSENT else v for v, h in zip(values, held, strict=True)]
                broken = [b or v is None for v, b in zip(values, broken, strict=True)]
                i += 1
            cells = [None if b else h for h, b in zip(held, broken, strict=True)]
            rows.append([*_stamp(session.start_ns, at), *cells])
    return [*TIME_COLUMNS, *(_column(s) for s in signals)], rows


def series_table(store: Store, session_id: int, address: str) -> tuple[list[str], list[list]]:
    session = store.session(session_id)
    series = store.series(session_id, address)
    rows = [[*_stamp(session.start_ns, p.offset_ns), p.value] for p in series.points]
    return [*TIME_COLUMNS, _column(series.signal)], rows


WRITE_FIELDS = ("value", "requested", "at_limit", "controller")


def writes_table(store: Store, session_id: int, address: str) -> tuple[list[str], list[list]]:
    session = store.session(session_id)
    rows = [
        [*_stamp(session.start_ns, w.offset_ns), *(getattr(w, f) for f in WRITE_FIELDS)]
        for w in store.write_states(session_id, address)
    ]
    return [*TIME_COLUMNS, *WRITE_FIELDS], rows


TICK_FIELDS = (
    "mode",
    "setpoint",
    "measured",
    "output",
    "expected",
    "correction",
    "delivered_correction",
)


def ticks_table(store: Store, session_id: int, controller: str) -> tuple[list[str], list[list]]:
    session = store.session(session_id)
    rows = [
        [*_stamp(session.start_ns, t.offset_ns), *(getattr(t, f) for f in TICK_FIELDS)]
        for t in store.ticks(session_id, controller)
    ]
    return [*TIME_COLUMNS, *TICK_FIELDS], rows


def events_table(store: Store, session_id: int) -> tuple[list[str], list[list]]:
    session = store.session(session_id)
    rows = [
        [
            *_stamp(session.start_ns, e.offset_ns),
            e.code,
            e.source or "",
            json.dumps(e.detail) if e.detail is not None else "",
        ]
        for e in store.events(session_id)
    ]
    return [*TIME_COLUMNS, "code", "source", "detail"], rows


# endregion

# region Routes


@router.get("/export")
def export_session(
    store: StoreDep,
    session_id: int,
    format: Literal["csv", "json", "zip"] = "csv",
    layout: Layout = "wide",
    step_s: float | None = Query(None, gt=0, description="Resample a wide table onto this grid"),
) -> Response:
    """Every signal as one table; ``zip`` adds each controller's ticks, every write, the events.

    ``wide`` holds each signal's last value on every row (one row per sample
    instant, or per ``step_s``); ``long`` is the raw samples, one per value.
    """
    if format != "zip":
        columns, rows = session_table(store, session_id, layout, step_s)
        body, media = _table(columns, rows, format)
        return _attachment(body, media, f"session-{session_id}-{layout}.{format}")

    session = store.session(session_id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, (columns, rows) in {
            "signals-wide.csv": session_table(store, session_id, "wide"),
            "signals-long.csv": session_table(store, session_id, "long"),
            "events.csv": events_table(store, session_id),
        }.items():
            archive.writestr(name, _table(columns, rows, "csv")[0])
        for controller in store.controllers(session_id):
            columns, rows = ticks_table(store, session_id, controller.name)
            archive.writestr(f"controller-{controller.name}.csv", _table(columns, rows, "csv")[0])
        for write in store.writes(session_id):
            columns, rows = writes_table(store, session_id, write.address)
            archive.writestr(f"write-{write.address}.csv", _table(columns, rows, "csv")[0])
        archive.writestr(
            "session.json",
            json.dumps(
                {
                    "id": session.id,
                    "start": _stamp(session.start_ns, 0)[1],
                    "end": None if session.end_ns is None else _stamp(session.end_ns, 0)[1],
                    "version": session.version,
                    "config": session.config,
                    "hardware": session.hardware,
                    "details": session.details,
                    "devices": [
                        {
                            "address": d.address,
                            "driver": d.driver,
                            "config": d.config,
                            "label": d.label,
                        }
                        for d in store.devices(session_id)
                    ],
                    "signals": [
                        {
                            "address": s.address,
                            "quantity": s.quantity,
                            "unit": s.unit,
                            "access": s.access,
                            "label": s.label,
                        }
                        for s in store.signals(session_id)
                    ],
                    "controllers": [
                        {
                            "name": c.name,
                            "measured": c.measured,
                            "law": c.law,
                            "feedforward": c.feedforward,
                        }
                        for c in store.controllers(session_id)
                    ],
                },
                indent=2,
            ),
        )
    return _attachment(buffer.getvalue(), "application/zip", f"session-{session_id}.zip")


@router.get("/series/{address}/export")
def export_series(
    store: StoreDep, session_id: int, address: str, format: Format = "csv"
) -> Response:
    columns, rows = series_table(store, session_id, address)
    body, media = _table(columns, rows, format)
    return _attachment(body, media, f"session-{session_id}-{address}.{format}")


@router.get("/writes/{address}/export")
def export_writes(
    store: StoreDep, session_id: int, address: str, format: Format = "csv"
) -> Response:
    columns, rows = writes_table(store, session_id, address)
    body, media = _table(columns, rows, format)
    return _attachment(body, media, f"session-{session_id}-write-{address}.{format}")


@router.get("/ticks/{controller}/export")
def export_ticks(
    store: StoreDep, session_id: int, controller: str, format: Format = "csv"
) -> Response:
    columns, rows = ticks_table(store, session_id, controller)
    body, media = _table(columns, rows, format)
    return _attachment(body, media, f"session-{session_id}-controller-{controller}.{format}")


@router.get("/events/export")
def export_events(store: StoreDep, session_id: int, format: Format = "csv") -> Response:
    columns, rows = events_table(store, session_id)
    body, media = _table(columns, rows, format)
    return _attachment(body, media, f"session-{session_id}-events.{format}")


# endregion
