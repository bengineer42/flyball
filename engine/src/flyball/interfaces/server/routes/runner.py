"""The process itself: how it was started, and -- where it allows -- stopping or restarting it.

Nothing here touches the rig; `flyball-runner` hands the server a handle to
the process before serving. A test client has none, so these answer 404.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from flyball.interfaces.server.deps import Runner, current_runner

router = APIRouter(prefix="/api/runner", tags=["runner"])


class RunnerOut(BaseModel):
    """The runner's settings as resolved, less the token, and the files it loaded."""

    endpoint: str | None
    """What the runner binds: `tcp:<host>:<port>`, or `unix:<path>` when a front started it."""
    root_path: str | None
    mcp: bool
    compose: bool
    allow_save: bool
    allow_shutdown: bool
    store: str | None
    programs: str | None
    tunings: str | None
    drivers: str | None
    files: list[str]
    keep: str
    """How much the scratch record holds while nothing is recorded, as configured (`1h`)."""
    keep_size: str
    retain: str
    rotate: str
    max_store: str
    keep_ns: int
    """The same five resolved: nanoseconds of the rig's clock and bytes; 0 for off / no cap."""
    keep_bytes: int
    retain_ns: int
    rotate_ns: int
    max_bytes: int


def _runner() -> Runner:
    runner = current_runner()
    if runner is None:
        raise HTTPException(status_code=404, detail="Not served by flyball-runner")
    return runner


def _may_stop() -> Runner:
    runner = _runner()
    if not runner.settings.allow_shutdown:
        raise HTTPException(
            status_code=409,
            detail="This runner was not started with --allow-shutdown (runner.allow_shutdown)",
        )
    return runner


@router.get("")
def read_runner() -> RunnerOut:
    """How this runner was started: what the API may do here, and where its files are."""
    runner = _runner()
    s = runner.settings
    return RunnerOut(
        endpoint=None if runner.exposure is None else runner.exposure.endpoint,
        root_path=s.root_path,
        mcp=s.mcp,
        compose=s.compose,
        allow_save=s.allow_save,
        allow_shutdown=s.allow_shutdown,
        store=None if s.store is None else str(s.store),
        programs=None if s.programs is None else str(s.programs),
        tunings=None if s.tunings is None else str(s.tunings),
        drivers=None if s.drivers is None else str(s.drivers),
        files=[str(f) for f in runner.files],
        keep=s.keep,
        keep_size=s.keep_size,
        retain=s.retain,
        rotate=s.rotate,
        max_store=s.max_store,
        keep_ns=s.keep_ns,
        keep_bytes=s.keep_bytes,
        retain_ns=s.retain_ns,
        rotate_ns=s.rotate_ns,
        max_bytes=s.max_bytes,
    )


@router.post("/shutdown", status_code=202)
def shutdown() -> dict[str, Any]:
    """Stop the runner: the rig's devices stop, a session closes, the process exits.

    409 unless started with `--allow-shutdown`.
    """
    _may_stop().shutdown()
    return {"detail": "shutting down"}


@router.post("/restart", status_code=202)
def restart() -> dict[str, Any]:
    """Stop as `shutdown` does, then start again with the same command line and files.

    A rig edit saved itself (to the overlay the next start loads, or, for a runner with no
    rig file, to the store, restarting with `--resume`), so it comes back; a controller
    attached since the start and not saved (`POST /api/rig/save`) does not. 409 unless
    started with `--allow-shutdown`; a rig edit's own restart does not need it.
    """
    _may_stop().restart()
    return {"detail": "restarting"}


__all__ = ["router"]
