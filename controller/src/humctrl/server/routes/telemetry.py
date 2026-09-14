"""Live push over websockets.

``/ws/samples`` forwards every sample the rig hears as it arrives.
``/ws/loops`` sends a snapshot of every loop once a second: loops change on
their own ticks, and a snapshot after the fact is what a dashboard wants --
demand and expected as the tick left them.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter

from humctrl.server.deps import current_rig, current_telemetry
from humctrl.server.schemas import LoopOut

router = APIRouter(tags=["telemetry"])

# How long a socket waits for a push before checking whether the rig went away,
# and the loop snapshot period.
IDLE_POLL_S = 1.0

SAMPLE = TypeAdapter(dict)
LOOPS = TypeAdapter(list[LoopOut])


async def _no_rig(websocket: WebSocket) -> None:
    await websocket.send_json({"error": "no rig attached"})
    await asyncio.sleep(IDLE_POLL_S)


@router.websocket("/ws/samples")
async def samples(websocket: WebSocket) -> None:
    """Every sample from every source, as published. Oldest dropped if the client lags."""
    await websocket.accept()
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            telemetry = current_telemetry()
            if telemetry is None:
                await _no_rig(websocket)
                continue
            with telemetry.samples.subscribe(maxsize=200) as queue:
                while current_telemetry() is telemetry:
                    try:
                        sample = await asyncio.wait_for(queue.get(), IDLE_POLL_S)
                    except TimeoutError:
                        continue
                    await websocket.send_json(sample.model_dump(mode="json"))


@router.websocket("/ws/loops")
async def loops(websocket: WebSocket) -> None:
    """A snapshot of every loop, once a second."""
    await websocket.accept()
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            rig = current_rig()
            if rig is None:
                await _no_rig(websocket)
                continue
            with rig.lock:
                view = [
                    LoopOut.of(ch, loop, loop.name == rig.loops.default)
                    for ch, loop in rig.loops.entries()
                ]
            await websocket.send_json(LOOPS.dump_python(view, mode="json"))
            await asyncio.sleep(IDLE_POLL_S)
