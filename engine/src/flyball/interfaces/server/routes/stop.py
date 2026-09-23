"""`POST /api/rig/stop`: stop the rig. Not wired yet: it answers 501.

Once wired it calls [current_stopper][flyball.interfaces.server.deps.current_stopper]'s
`stop` on a worker thread and answers its [StopReport][flyball.rig.stopping.StopReport].
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/rig", tags=["rig"])


class StopIn(BaseModel):
    reason: str | None = None


@router.post("/stop", status_code=501)
def stop(body: StopIn | None = None) -> JSONResponse:
    """Stop the rig: the program interrupted, controllers to manual, each device stopped."""
    return JSONResponse(status_code=501, content={"detail": "stop not wired yet"})
