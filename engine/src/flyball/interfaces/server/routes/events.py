"""What has happened: the recent events, and a stream of them as they occur.

An [Event][flyball.foundation.device.state.Event] is a point in time -- a step failed, a
reader went offline, a pump clamped a request. `/api/events` is the last few
hundred; `/ws/events` sends those on connect, then each new one.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter

from flyball.foundation.device import Event, Level
from flyball.interfaces.server.deps import RigDep, current_rig
from flyball.interfaces.server.routes.telemetry import IDLE_POLL_S, _closed, _no_rig

router = APIRouter(tags=["events"])

EVENT = TypeAdapter(Event)


def event_out(event: Event) -> dict[str, Any]:
    out: dict[str, Any] = EVENT.dump_python(event, mode="json")
    out["level"] = Level(event.level).name
    return out


@router.get("/api/events")
def read_events(rig: RigDep, limit: int = 100, level: str | None = None) -> list[dict[str, Any]]:
    """The most recent events, oldest first; `level` keeps that level and above."""
    floor = Level[level.upper()] if level else Level.DEBUG
    recent = [e for e in rig.recent if e.level >= floor]
    return [event_out(e) for e in recent[-limit:]]


@router.websocket("/ws/events")
async def events(websocket: WebSocket) -> None:
    """Recent events on connect, then each as it happens."""
    await websocket.accept()
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            rig = current_rig()
            if rig is None:
                await _no_rig(websocket)
                continue
            closed = asyncio.ensure_future(_closed(websocket))
            with rig.events.subscribe(maxsize=200) as queue:
                await websocket.send_json({"events": [event_out(e) for e in rig.recent]})
                getter: asyncio.Future[Any] = asyncio.ensure_future(queue.get())
                try:
                    while current_rig() is rig:
                        done, _ = await asyncio.wait(
                            {closed, getter},
                            timeout=IDLE_POLL_S,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if closed in done:
                            raise WebSocketDisconnect
                        if getter in done:
                            await websocket.send_json({"events": [event_out(getter.result())]})
                            getter = asyncio.ensure_future(queue.get())
                finally:
                    getter.cancel()
                    closed.cancel()
