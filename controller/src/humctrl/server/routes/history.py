"""What was recorded: sessions, series to plot, ticks, events, spans, tunings.

Reads the store, never the rig, so it works with no hardware attached and on
a copy of the database taken from another machine. Times in the store are
integer nanosecond offsets from the session start; the wire carries the same
integers -- a client converts once, and nothing is rounded on the way out.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from humctrl.db import (
    ActuatorRow,
    ChannelRow,
    Downsample,
    Event,
    LoopRow,
    Series,
    SessionRow,
    SourceRow,
    Span,
    Tick,
    TuningRow,
    Window,
)
from humctrl.server.deps import StoreDep

router = APIRouter(prefix="/api/history", tags=["history"])


def _window(start_ns: int | None, end_ns: int | None) -> Window | None:
    return None if start_ns is None and end_ns is None else Window(start_ns, end_ns)


# region Sessions


@router.get("/sessions")
async def read_sessions(store: StoreDep, limit: int | None = Query(None, ge=1)) -> list[SessionRow]:
    """Newest first."""
    return store.sessions(limit)


@router.get("/sessions/{session_id}")
async def read_session(store: StoreDep, session_id: int) -> SessionRow:
    return store.session(session_id)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(store: StoreDep, session_id: int) -> None:
    """Everything the session recorded goes with it. Tunings made in it survive."""
    store.delete_session(session_id)


@router.get("/sessions/{session_id}/sources")
async def read_sources(store: StoreDep, session_id: int) -> list[SourceRow]:
    return store.sources(session_id)


@router.get("/sessions/{session_id}/channels")
async def read_channels(store: StoreDep, session_id: int) -> list[ChannelRow]:
    return store.channels(session_id)


@router.get("/sessions/{session_id}/actuators")
async def read_actuators(store: StoreDep, session_id: int) -> list[ActuatorRow]:
    return store.actuators(session_id)


@router.get("/sessions/{session_id}/loops")
async def read_loops(store: StoreDep, session_id: int) -> list[LoopRow]:
    return store.loops(session_id)


# endregion

# region Data


@router.get("/sessions/{session_id}/series/{source}/{quantity}")
async def read_series(
    store: StoreDep,
    session_id: int,
    source: str,
    quantity: str,
    start_ns: int | None = None,
    end_ns: int | None = None,
    every: int | None = Query(None, ge=1, description="Keep every nth sample"),
    bucket_ns: int | None = Query(None, ge=1, description="Average each bucket of this size"),
    max_points: int | None = Query(None, ge=2, description="Average into at most this many points"),
) -> Series:
    """One channel over a window, optionally downsampled one of three ways.

    ``every`` keeps real readings and is cheapest; ``bucket_ns`` and
    ``max_points`` average, which is what a plot of a given width wants. The
    response says which was applied and the bucket size actually used.
    """
    given = {
        k: v
        for k, v in dict(every=every, bucket_ns=bucket_ns, max_points=max_points).items()
        if v is not None
    }
    downsample = Downsample(**given) if given else None
    return store.series(session_id, source, quantity, _window(start_ns, end_ns), downsample)


@router.get("/sessions/{session_id}/ticks/{loop}")
async def read_ticks(
    store: StoreDep,
    session_id: int,
    loop: str,
    start_ns: int | None = None,
    end_ns: int | None = None,
) -> list[Tick]:
    return store.ticks(session_id, loop, _window(start_ns, end_ns))


@router.get("/sessions/{session_id}/events")
async def read_events(
    store: StoreDep,
    session_id: int,
    start_ns: int | None = None,
    end_ns: int | None = None,
    kind: str | None = None,
) -> list[Event]:
    return store.events(session_id, _window(start_ns, end_ns), kind)


@router.get("/sessions/{session_id}/spans")
async def read_spans(store: StoreDep, session_id: int) -> list[Span]:
    """In start order; nest by ``parent_id``."""
    return store.spans(session_id)


# endregion

# region Tunings


class SaveTuning(BaseModel):
    law: str
    config: dict[str, Any]
    created_ns: int
    session_id: int | None = None
    loop: str | None = None
    notes: Any = None


@router.get("/tunings")
async def read_tunings(store: StoreDep) -> list[TuningRow]:
    """The newest version of every name."""
    return store.tunings()


@router.get("/tunings/{name}")
async def read_tuning(store: StoreDep, name: str) -> TuningRow:
    return store.tuning(name)


@router.get("/tunings/{name}/history")
async def read_tuning_history(store: StoreDep, name: str) -> list[TuningRow]:
    """Every version, newest first."""
    return store.tuning_history(name)


@router.put("/tunings/{name}", status_code=201)
async def save_tuning(store: StoreDep, name: str, body: SaveTuning) -> TuningRow:
    """Add a version under ``name``; earlier versions are kept."""
    return store.save_tuning(
        name, body.law, body.config, body.created_ns, body.session_id, body.loop, body.notes
    )


@router.delete("/tunings/{name}", status_code=204)
async def delete_tuning(store: StoreDep, name: str) -> None:
    """Every version."""
    store.delete_tuning(name)


# endregion
