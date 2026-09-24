"""`POST /api/rig/stop`: the software stop; its latch, its Reset, and what it would write.

`POST /api/rig/stop` calls [current_stopper][flyball.interfaces.server.deps.current_stopper]'s
`stop` on a worker thread and answers its [StopReport][flyball.rig.stopping.StopReport].
It needs the operator verb (the door's verb table says so). Nothing else refuses it: no
rate limit, and no body it cannot read -- a missing or unreadable `reason` is `""`, a
long one is cut.

`POST /api/rig/reset` lets a latch go (a stop's, or a controller's `on_fault`): operate,
and a person -- an agent through MCP or a service token is refused. `GET /api/rig/stop`
is what a stop would write to each output, and why; `GET /api/rig/latches` what holds.
"""

from __future__ import annotations

import json
from typing import Any

from anyio import CapacityLimiter, to_thread
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from flyball.interfaces.server.deps import RigDep, current_stopper
from flyball.rig.stopping import Actor, StopReport, stop_plan

router = APIRouter(prefix="/api/rig", tags=["rig"])

REASON_CHARS = 500
"""A reason longer than this is cut to it."""

STOP_SLOTS = CapacityLimiter(4)
"""Stops' own worker threads: a stop does not queue behind the threadpool other routes share."""

_BODY = {
    "required": False,
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"reason": {"type": "string", "maxLength": REASON_CHARS}},
            }
        }
    },
}


@router.post("/stop", openapi_extra={"requestBody": _BODY})
async def stop(request: Request) -> StopReport:
    """Stop the rig: latched, then each device's resolved stop written.

    First the latch, then long commands cancelled, the program interrupted and every
    controller to manual. Each device is reported `stopped` (its stop command ran, or
    it wrote its stop values), `unchanged` (every output kept) or `failed` (with `may
    still act` after the time ran out). The rig stays latched -- refusing automatic
    writes -- until a person resets it (`POST /api/rig/reset`). 503 with no rig.
    """
    stopper = current_stopper()
    if stopper is None:
        raise HTTPException(status_code=503, detail="No rig attached: nothing to stop")
    reason = _reason(await request.body())
    return await to_thread.run_sync(stopper.stop, actor(request), reason, limiter=STOP_SLOTS)


class ResetBody(BaseModel):
    """Which latch to let go: `stop` (the rig stop's), or `on_fault:<controller>`."""

    cause: str = "stop"


@router.post("/reset")
def reset(rig: RigDep, request: Request, body: ResetBody | None = None) -> dict[str, Any]:
    """Let a latch go: nothing resumes -- controllers stay in manual, programs stay ended.

    Needs operate and a person: 403 for an agent (through MCP) or a service token. 404
    when nothing holds for `cause`. A fault's Reset clears its controller's law. Answers
    the latch let go.
    """
    who = actor(request)
    if not who.person:
        raise HTTPException(
            status_code=403,
            detail=f"a Reset needs a person; {who.sub} ({who.kind} via {who.via}) may not"
            " reset a latch",
        )
    cause = "stop" if body is None else body.cause
    return rig.stopping.reset(cause, who).as_dict()


@router.get("/stop")
async def read_stop(rig: RigDep) -> dict[str, Any]:
    """What a stop would do to each writable output, sorted `off`, `you said`, `keep`.

    `stop` is the value it writes (`keep`: left as it is; null: the device's stop
    command runs), `source` who said so (`off`: the driver's inactive level; `you said`:
    the rig file's `stop:`; `nobody said`; `command`). `warnings` flags a controller's
    output left energised, and an unbounded freeze on an output whose stop is `off`.
    `covered_if_flyball_dies` is false for every output: nothing here acts if flyball
    is not running. `stopped` is the rig stop's latch, if it holds.
    """
    stopped = rig.stopping.latches.rig_stop
    return {
        "stopped": None if stopped is None else stopped.as_dict(),
        "outputs": stop_plan(rig),
    }


@router.get("/latches")
async def read_latches(rig: RigDep) -> list[dict[str, Any]]:
    """Every latch held now: its cause, the subjects it holds, who set it, when and why."""
    return [latch.as_dict() for latch in rig.stopping.latches.all()]


def _reason(body: bytes) -> str:
    try:
        document: Any = json.loads(body) if body else None
    except ValueError:
        return ""
    reason = document.get("reason") if isinstance(document, dict) else None
    return reason[:REASON_CHARS] if isinstance(reason, str) else ""


def actor(request: Request) -> Actor:
    """Who is asking, from the principal the door put on the request.

    A principal with `sub`/`sid`/`kind` (v1) is taken as it is; today's door's
    (`scheme`, `level`) becomes `sub` = the scheme, `kind` = `service` for a token.
    """
    principal = getattr(request.state, "auth", None)
    peer = f"from {request.client.host}" if request.client is not None else ""
    sub = getattr(principal, "sub", None)
    if isinstance(sub, str):
        via = "mcp" if getattr(principal, "via", None) == "mcp" else "http"
        sid, kind = getattr(principal, "sid", ""), getattr(principal, "kind", "")
        return Actor(sub=sub, sid=str(sid), kind=str(kind), via=via, detail=peer)
    scheme = str(getattr(principal, "scheme", "anonymous"))
    kind = "service" if scheme == "token" else "human"
    return Actor(sub=scheme, sid="", kind=kind, via="http", detail=peer)
