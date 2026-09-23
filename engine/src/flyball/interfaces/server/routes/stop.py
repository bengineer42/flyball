"""`POST /api/rig/stop`: stop the rig.

It calls [current_stopper][flyball.interfaces.server.deps.current_stopper]'s `stop` on a
worker thread and answers its [StopReport][flyball.rig.stopping.StopReport]. It needs the
operator verb (the door's verb table says so). Nothing else refuses it: no rate limit,
and no body it cannot read -- a missing or unreadable `reason` is `""`, a long one is cut.
"""

from __future__ import annotations

import json
from typing import Any

from anyio import CapacityLimiter, to_thread
from fastapi import APIRouter, HTTPException, Request

from flyball.interfaces.server.deps import current_stopper
from flyball.rig.stopping import Actor, StopReport

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
    """Stop the rig: the program interrupted, every controller to manual, each device stopped.

    Until the signals work lands the stop is the interim one (`interim: true`): nothing
    is written, so each writable device is `held` at its last value. 503 with no rig.
    """
    stopper = current_stopper()
    if stopper is None:
        raise HTTPException(status_code=503, detail="No rig attached: nothing to stop")
    reason = _reason(await request.body())
    return await to_thread.run_sync(stopper.stop, _actor(request), reason, limiter=STOP_SLOTS)


def _reason(body: bytes) -> str:
    try:
        document: Any = json.loads(body) if body else None
    except ValueError:
        return ""
    reason = document.get("reason") if isinstance(document, dict) else None
    return reason[:REASON_CHARS] if isinstance(reason, str) else ""


def _actor(request: Request) -> Actor:
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
