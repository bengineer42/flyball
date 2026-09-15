"""Start and stop recording on the live rig; what is being recorded now.

Everything *about* a session once it exists is under ``/api/history``, which
reads the store. This is the one place the rig and the store meet: opening a
session needs both.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from flyball.core.errors import ConflictError
from flyball.db import SessionRow
from flyball.server.deps import RigDep, StoreDep

router = APIRouter(prefix="/api/recording", tags=["recording"])


class StartRecording(BaseModel):
    """What to note about the session; ``details`` is free-form (a name, notes, tags)."""

    details: Any = None
    version: str | None = None
    config: Any = None
    hardware: Any = None


def _current(rig: RigDep) -> SessionRow | None:
    recorder = rig.recorder
    return None if recorder is None else recorder.writer.session


@router.get("")
async def read_recording(rig: RigDep) -> SessionRow | None:
    """The open session, or null when not recording."""
    return _current(rig)


@router.post("", status_code=201)
def start_recording(rig: RigDep, store: StoreDep, body: StartRecording | None = None) -> SessionRow:
    """Open a session and record into it. 409 while one is already open: end it first."""
    if _current(rig) is not None:
        raise ConflictError("already recording; end the open session first")
    fields = (body or StartRecording()).model_dump(exclude_none=True)
    return rig.start_recording(store, **fields).writer.session


@router.post("/end")
def end_recording(rig: RigDep) -> SessionRow:
    """Close the open session. 409 when nothing is recording."""
    session = _current(rig)
    if session is None:
        raise ConflictError("not recording")
    rig.stop_recording()
    return session
