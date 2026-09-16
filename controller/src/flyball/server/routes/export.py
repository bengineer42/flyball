"""Downloads: a session, one channel, one loop or the events as a file.

Every export is a plain table with the same two leading time columns --
``time_s`` (seconds since the session started) and ``time`` (ISO 8601, UTC) --
so files from one session line up in a spreadsheet. ``format`` is ``csv`` or
``json``; a whole session can also come as a ``zip`` of every table plus the
session's metadata.

A ``wide`` session table has one column per channel and one row per instant
(or per ``step_s``), each channel holding its last value; ``long`` has one row
per raw value (``source, measurand, unit, value``).
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

from flyball.db import Store
from flyball.server.deps import StoreDep

router = APIRouter(prefix="/api/history/sessions/{session_id}", tags=["history"])

Format = Literal["csv", "json"]
Layout = Literal["wide", "long"]

TIME_COLUMNS = ("time_s", "time")


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


def _column(channel: Any) -> str:
    return f"{channel.name} ({channel.measurand.unit})"


# region Tables


def session_table(
    store: Store, session_id: int, layout: Layout, step_s: float | None = None
) -> tuple[list[str], list[list]]:
    session = store.session(session_id)
    channels = store.channels(session_id)
    if layout == "long":
        rows: list[list] = []
        for source in store.sources(session_id):
            units = {
                c.measurand.name: c.measurand.unit for c in channels if c.source.id == source.id
            }
            for sample in store.samples(session_id, source.name):
                stamp = _stamp(session.start_ns, sample.offset_ns)
                for measurand, value in sample.values.items():
                    rows.append([*stamp, source.name, measurand, units.get(measurand, ""), value])
        rows.sort(key=lambda r: r[0])
        return [*TIME_COLUMNS, "source", "measurand", "unit", "value"], rows

    order = {c.name: i for i, c in enumerate(channels)}
    by_instant: dict[int, list] = {}
    for source in store.sources(session_id):
        for sample in store.samples(session_id, source.name):
            row = by_instant.setdefault(sample.offset_ns, [None] * len(channels))
            for measurand, value in sample.values.items():
                index = order.get(f"{source.name}.{measurand}")
                if index is not None:
                    row[index] = value
    # Sources sample on their own clocks, milliseconds apart, so a row per raw
    # instant would be mostly blank: each row carries the last value of every
    # channel instead, and `step_s` resamples that onto a regular grid.
    held: list = [None] * len(channels)
    rows: list[list] = []
    instants = sorted(by_instant)
    if step_s is None:
        for at in instants:
            held = [v if v is not None else h for v, h in zip(by_instant[at], held, strict=True)]
            rows.append([*_stamp(session.start_ns, at), *held])
    elif instants:
        step_ns = int(step_s * 1e9)
        i = 0
        for at in range(0, instants[-1] + 1, step_ns):
            while i < len(instants) and instants[i] <= at:
                held = [
                    v if v is not None else h
                    for v, h in zip(by_instant[instants[i]], held, strict=True)
                ]
                i += 1
            rows.append([*_stamp(session.start_ns, at), *held])
    return [*TIME_COLUMNS, *(_column(c) for c in channels)], rows


def series_table(
    store: Store, session_id: int, source: str, measurand: str
) -> tuple[list[str], list[list]]:
    session = store.session(session_id)
    series = store.series(session_id, source, measurand)
    rows = [[*_stamp(session.start_ns, p.offset_ns), p.value] for p in series.points]
    return [*TIME_COLUMNS, _column(series.channel)], rows


TICK_FIELDS = (
    "mode",
    "setpoint",
    "reading",
    "demand",
    "expected",
    "correction",
    "delivered_correction",
)


def ticks_table(store: Store, session_id: int, loop: str) -> tuple[list[str], list[list]]:
    session = store.session(session_id)
    rows = [
        [*_stamp(session.start_ns, t.offset_ns), *(getattr(t, f) for f in TICK_FIELDS)]
        for t in store.ticks(session_id, loop)
    ]
    return [*TIME_COLUMNS, *TICK_FIELDS], rows


def events_table(store: Store, session_id: int) -> tuple[list[str], list[list]]:
    session = store.session(session_id)
    rows = [
        [
            *_stamp(session.start_ns, e.offset_ns),
            e.kind,
            e.source or "",
            json.dumps(e.detail) if e.detail is not None else "",
        ]
        for e in store.events(session_id)
    ]
    return [*TIME_COLUMNS, "kind", "source", "detail"], rows


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
    """Every channel as one table; ``zip`` adds each loop's ticks, the events and the metadata.

    ``wide`` holds each channel's last value on every row (one row per sample
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
            "channels-wide.csv": session_table(store, session_id, "wide"),
            "channels-long.csv": session_table(store, session_id, "long"),
            "events.csv": events_table(store, session_id),
        }.items():
            archive.writestr(name, _table(columns, rows, "csv")[0])
        for loop in store.loops(session_id):
            columns, rows = ticks_table(store, session_id, loop.name)
            archive.writestr(f"loop-{loop.name}.csv", _table(columns, rows, "csv")[0])
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
                    "channels": [
                        {"name": c.name, "unit": c.measurand.unit, "label": c.measurand.label}
                        for c in store.channels(session_id)
                    ],
                    "loops": [
                        {
                            "name": loop.name,
                            "channel": loop.channel.name,
                            "law": loop.config,
                            "feedforward": loop.feedforward,
                        }
                        for loop in store.loops(session_id)
                    ],
                },
                indent=2,
            ),
        )
    return _attachment(buffer.getvalue(), "application/zip", f"session-{session_id}.zip")


@router.get("/series/{source}/{measurand}/export")
def export_series(
    store: StoreDep, session_id: int, source: str, measurand: str, format: Format = "csv"
) -> Response:
    columns, rows = series_table(store, session_id, source, measurand)
    body, media = _table(columns, rows, format)
    return _attachment(body, media, f"session-{session_id}-{source}.{measurand}.{format}")


@router.get("/ticks/{loop}/export")
def export_ticks(store: StoreDep, session_id: int, loop: str, format: Format = "csv") -> Response:
    columns, rows = ticks_table(store, session_id, loop)
    body, media = _table(columns, rows, format)
    return _attachment(body, media, f"session-{session_id}-loop-{loop}.{format}")


@router.get("/events/export")
def export_events(store: StoreDep, session_id: int, format: Format = "csv") -> Response:
    columns, rows = events_table(store, session_id)
    body, media = _table(columns, rows, format)
    return _attachment(body, media, f"session-{session_id}-events.{format}")


# endregion
