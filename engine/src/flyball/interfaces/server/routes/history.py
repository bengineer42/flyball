"""What was recorded: sessions, series, ticks, events, spans, tunings.

Reads the store, never the rig, so it works without hardware and on a copied
database. Times are integer nanosecond offsets from the session start, on the
wire as in the store.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from flyball.foundation.errors import ConflictError, NotFoundError
from flyball.interfaces.server.deps import StoreDep, current_rig
from flyball.record import (
    ControllerRow,
    DeviceRow,
    Downsample,
    Event,
    Series,
    SessionKind,
    SessionRow,
    SignalRow,
    Span,
    Tick,
    TuningRow,
    Window,
    WriteRow,
    WriteStateRow,
)
from flyball.record.documents import documents
from flyball.record.errors import NotDeclaredError

router = APIRouter(prefix="/api/history", tags=["history"])


def _window(start_ns: int | None, end_ns: int | None) -> Window | None:
    return None if start_ns is None and end_ns is None else Window(start_ns, end_ns)


# region Sessions


class KeepRange(BaseModel):
    """A range of a session -- the scratch record, usually -- to keep as a session of its own."""

    start_ns: int
    end_ns: int
    details: Any = None


class SessionPatch(BaseModel):
    pinned: bool | None = None
    name: str | None = None


def _being_written(session_id: int) -> SessionRow | None:
    """The rig's open session if it is this one: not to be deleted from under its writer."""
    rig = current_rig()
    recorder = rig.recorder if rig is not None else None
    if recorder is not None and recorder.writer.session.id == session_id:
        return recorder.writer.session
    return None


@router.get("/sessions")
async def read_sessions(
    store: StoreDep,
    limit: int | None = Query(None, ge=1),
    kind: Annotated[
        SessionKind | None, Query(description="Only recordings, or only scratch")
    ] = None,
) -> list[SessionRow]:
    """Newest first. The runner's scratch record is among them, `kind: "scratch"`."""
    return store.sessions(limit, kind)


@router.get("/sessions/{session_id}")
async def read_session(store: StoreDep, session_id: int) -> SessionRow:
    return store.session(session_id)


@router.post("/sessions/{session_id}/end")
def end_session(store: StoreDep, session_id: int) -> SessionRow:
    """Close an open session.

    The one being recorded right now is closed through the rig, so the
    recorder stops cleanly; one left open by a runner that died is closed in
    the store at the time of its last sample. 409 if it is already ended.
    """
    if (writing := _being_written(session_id)) is not None:
        if writing.scratch:
            raise ConflictError("the scratch record is not a recording; it is trimmed, not ended")
        current_rig().stop_recording()  # type: ignore[union-attr]
        return store.session(session_id)
    return store.end_session(session_id)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(store: StoreDep, session_id: int) -> None:
    """Everything the session recorded goes with it. Tunings made in it survive.

    409 for the session being recorded right now, and for the scratch record
    while the runner keeps it: end the recording first; scratch trims itself.
    """
    if (writing := _being_written(session_id)) is not None:
        raise ConflictError(
            "the scratch record cannot be deleted while the runner keeps it"
            if writing.scratch
            else "this session is being recorded; end it first"
        )
    store.delete_session(session_id)


@router.patch("/sessions/{session_id}")
@router.put("/sessions/{session_id}")
async def update_session(store: StoreDep, session_id: int, body: SessionPatch) -> SessionRow:
    """Pin/unpin and/or rename, either field or both in one call.

    Pinning means a session is never aged out by `retain` or `max_store`; a
    rename sets `details.name`, what `sessionName` shows.
    """
    row = store.session(session_id)
    if body.pinned is not None:
        row = store.set_pinned(session_id, body.pinned)
    if body.name is not None:
        row = store.set_session_name(session_id, body.name)
    return row


@router.post("/sessions/{session_id}/keep", status_code=201)
async def keep_range(store: StoreDep, session_id: int, body: KeepRange) -> SessionRow:
    """Copy a range of a session into a new, closed session of its own, and return it.

    Made for the scratch record: `start_ns` and `end_ns` are absolute, in the
    rig's clock; the readings, write states, ticks and events within come
    over, and the declarations whole. 422 when the range is empty or
    reaches outside what the source holds.
    """
    rig = current_rig()
    if rig is not None and body.end_ns > rig.clock.now_ns():
        raise ValueError(f"the range ends in the future; it is now {rig.clock.now_ns()}")
    return store.keep_range(session_id, body.start_ns, body.end_ns, body.details)


@router.get("/sessions/{session_id}/documents")
async def read_session_documents(store: StoreDep, session_id: int) -> list[Any]:
    """The session as Bluesky event-model documents: `[[name, doc], ...]` in order."""
    return [[name, doc] for name, doc in documents(store, session_id)]


@router.get("/sessions/{session_id}/devices")
async def read_devices(store: StoreDep, session_id: int) -> list[DeviceRow]:
    return store.devices(session_id)


@router.get("/sessions/{session_id}/signals")
async def read_signals(store: StoreDep, session_id: int) -> list[SignalRow]:
    """Every signal the session recorded, by device; `address` keys the series and writes."""
    return store.signals(session_id)


@router.get("/sessions/{session_id}/writes")
async def read_writes(store: StoreDep, session_id: int) -> list[WriteRow]:
    """The writable signals whose write states the session recorded."""
    return store.writes(session_id)


@router.get("/sessions/{session_id}/controllers")
async def read_controllers(store: StoreDep, session_id: int) -> list[ControllerRow]:
    return store.controllers(session_id)


# endregion

# region Data


@router.get("/sessions/{session_id}/series/{address}")
async def read_series(
    store: StoreDep,
    session_id: int,
    address: str,
    start_ns: int | None = None,
    end_ns: int | None = None,
    every: int | None = Query(None, ge=1, description="Keep every nth sample"),
    bucket_ns: int | None = Query(None, ge=1, description="Average each bucket of this size"),
    max_points: int | None = Query(None, ge=2, description="Average into at most this many points"),
) -> Series:
    """One signal over a window, optionally downsampled.

    `every` keeps real readings; `bucket_ns` and `max_points` average. The
    response says which was applied and the bucket size used.
    """
    given = {
        k: v
        for k, v in dict(every=every, bucket_ns=bucket_ns, max_points=max_points).items()
        if v is not None
    }
    downsample = Downsample(**given) if given else None
    try:
        return store.series(session_id, address, _window(start_ns, end_ns), downsample)
    except NotDeclaredError as e:  # the rig may have it; this session never recorded it
        raise NotFoundError(str(e)) from e


@router.get("/sessions/{session_id}/writes/{address}")
async def read_write_states(
    store: StoreDep,
    session_id: int,
    address: str,
    start_ns: int | None = None,
    end_ns: int | None = None,
) -> list[WriteStateRow]:
    """What one writable signal was set to, in order."""
    return store.write_states(session_id, address, _window(start_ns, end_ns))


@router.get("/sessions/{session_id}/ticks/{controller}")
async def read_ticks(
    store: StoreDep,
    session_id: int,
    controller: str,
    start_ns: int | None = None,
    end_ns: int | None = None,
    every: int | None = Query(None, ge=1, description="Keep one tick in every n"),
) -> list[Tick]:
    """A controller's steps; `controller` is the address of the signal it drives."""
    return store.ticks(session_id, controller, _window(start_ns, end_ns), every)


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
    """In start order; nest by `parent_id`."""
    return store.spans(session_id)


# endregion

# region Tunings


class SaveTuning(BaseModel):
    law: str
    config: dict[str, Any]
    created_ns: int
    session_id: int | None = None
    controller: str | None = None
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
    """Add a version under `name`; earlier versions are kept."""
    return store.save_tuning(
        name, body.law, body.config, body.created_ns, body.session_id, body.controller, body.notes
    )


@router.delete("/tunings/{name}", status_code=204)
async def delete_tuning(store: StoreDep, name: str) -> None:
    """Every version."""
    store.delete_tuning(name)


# endregion
