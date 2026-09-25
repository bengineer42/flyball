"""Start and stop recording on the live rig; what is being recorded now.

Everything *about* a session once it exists is under ``/api/history``, which
reads the store. Sessions themselves are the recorder's
(`flyball.runtime.recorder.Recorder`): these routes ask it to open and end
them, and it fills in what produced each (`flyball_version`, `packages`,
`hardware`).

The runner's scratch record (the rolling last `keep`, kept while nothing is
being recorded) is not a recording: ``GET`` answers null while only it runs,
and starting a session replaces it -- with as much of it as ``include_ns``
asks for copied into the new session first.
"""

from __future__ import annotations

from threading import Lock
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from flyball.foundation.errors import ConflictError
from flyball.interfaces.server.deps import RecorderDep, RigDep, StoreDep, current_recorder
from flyball.record import SessionRow
from flyball.runtime.recorder import Recorder

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


def _current(recorder: Recorder | None) -> SessionRow | None:
    """The open session, re-read from the store: the writer's row is as it was at open."""
    session = None if recorder is None else recorder.recording
    if recorder is None or session is None:
        return None
    return recorder.store.session(session.writer.session.id)


@router.get("")
def read_recording(rig: RigDep) -> SessionRow | None:
    """The open session, or null when not recording."""
    return _current(current_recorder())


_starting = Lock()
"""One start at a time: the 409 check and the open are one step, or two starts both pass it."""


@router.post("", status_code=201)
def start_recording(
    rig: RigDep, store: StoreDep, recorder: RecorderDep, body: StartRecording | None = None
) -> SessionRow:
    """Open a session and record into it. 409 while one is already open: end it first."""
    with _starting:
        return _start(rig, recorder, body)


def _start(rig: RigDep, recorder: Recorder, body: StartRecording | None) -> SessionRow:
    if _current(recorder) is not None:
        raise ConflictError("already recording; end the open session first")
    fields = (body or StartRecording()).model_dump(exclude_none=True)
    # A runner started with `--record` stores the whole rig file as `config`,
    # which is where the sessions list reads the rig's name from. A session
    # opened from here otherwise carries none at all: give it at least the
    # name, so it doesn't look unnamed next to a runner-recorded session.
    if "config" not in fields and rig.name:
        fields["config"] = {"name": rig.name}
    return recorder.start_session(**fields).writer.session  # replaces the scratch record


@router.post("/end")
def end_recording(store: StoreDep, recorder: RecorderDep) -> SessionRow:
    """Close the open session. 409 when nothing is recording."""
    if _current(recorder) is None:
        raise ConflictError("not recording")
    ended = recorder.end_session()
    assert ended is not None
    return ended
