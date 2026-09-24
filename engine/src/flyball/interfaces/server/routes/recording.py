"""Start and stop recording on the live rig; what is being recorded now.

Everything *about* a session once it exists is under ``/api/history``, which
reads the store. This is the one place the rig and the store meet: opening a
session needs both.

The runner's scratch record (the rolling last `keep`, kept while nothing is
being recorded) is not a recording: ``GET`` answers null while only it runs,
and starting a session replaces it -- with as much of it as ``include_ns``
asks for copied into the new session first.
"""

from __future__ import annotations

from threading import Lock
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from flyball.foundation.errors import ConflictError
from flyball.interfaces.server.deps import RigDep, StoreDep, get_store
from flyball.record import SessionRow

router = APIRouter(prefix="/api/recording", tags=["recording"])


class StartRecording(BaseModel):
    """What to note about the session; ``details`` is free-form (a name, notes, tags)."""

    details: Any = None
    flyball_version: str | None = None
    config: Any = None
    hardware: Any = None
    include_ns: int | None = Field(
        default=None,
        ge=0,
        description="Start the session this far back, in the rig's clock, filled from the"
        " scratch record: what was just watched is kept. Clamped to what scratch holds.",
    )


def _current(rig: RigDep) -> SessionRow | None:
    """The open session, re-read from the store: the writer's row is as it was at open."""
    recorder = rig.recording
    if recorder is None:
        return None
    try:
        return get_store().session(recorder.writer.session.id)
    except HTTPException:  # no store attached: the row we have
        return recorder.writer.session


@router.get("")
def read_recording(rig: RigDep) -> SessionRow | None:
    """The open session, or null when not recording."""
    return _current(rig)


_starting = Lock()
"""One start at a time: the 409 check and the open are one step, or two starts both pass it."""


@router.post("", status_code=201)
def start_recording(rig: RigDep, store: StoreDep, body: StartRecording | None = None) -> SessionRow:
    """Open a session and record into it. 409 while one is already open: end it first."""
    with _starting:
        return _start(rig, store, body)


def _start(rig: RigDep, store: StoreDep, body: StartRecording | None) -> SessionRow:
    if _current(rig) is not None:
        raise ConflictError("already recording; end the open session first")
    fields = (body or StartRecording()).model_dump(exclude_none=True)
    # A runner started with `--record` stores the whole rig file as `config`,
    # which is where the sessions list reads the rig's name from. A session
    # opened from here otherwise carries none at all: give it at least the
    # name, so it doesn't look unnamed next to a runner-recorded session.
    if "config" not in fields and rig.name:
        fields["config"] = {"name": rig.name}
    include_ns = fields.pop("include_ns", None)
    scratch = rig.recorder.writer.session if rig.recorder is not None else None
    if include_ns and scratch is not None:
        held_from = store.session(scratch.id).start_ns  # trimmed since it was opened
        fields["start_ns"] = max(rig.clock.now_ns() - include_ns, held_from)
    recorder = rig.start_recording(store, **fields)  # closes the scratch record
    session = recorder.writer.session
    if include_ns and scratch is not None:
        closed = store.session(scratch.id)  # ended just now, at the new session's first instant
        store.backfill(session.id, scratch.id, session.start_ns, (closed.end_ns or 0) + 1)
    return session


@router.post("/end")
def end_recording(rig: RigDep, store: StoreDep) -> SessionRow:
    """Close the open session. 409 when nothing is recording."""
    session = _current(rig)
    if session is None:
        raise ConflictError("not recording")
    rig.stop_recording()
    return store.session(session.id)  # re-read: the row we held was taken before it closed
