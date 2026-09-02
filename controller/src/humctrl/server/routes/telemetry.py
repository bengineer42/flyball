"""Live state push.

Subscribes to the manager's own topics rather than keeping a parallel one, so
the server and the control loop cannot disagree about what was published.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from humctrl.manager import Manager
from humctrl.server.deps import current_manager
from humctrl.server.snapshots import readings_state, rig_state

router = APIRouter(tags=["telemetry"])

# How long a socket waits for a push before checking whether the rig went away.
IDLE_POLL_S = 1.0


async def _wait_for_rig(websocket: WebSocket) -> Manager | None:
    """Tell the client there is no rig, then let it keep the socket open."""
    await websocket.send_json({"error": "no rig attached"})
    await asyncio.sleep(IDLE_POLL_S)
    return current_manager()


@router.websocket("/ws/telemetry")
async def telemetry(websocket: WebSocket) -> None:
    """Full rig state, pushed whenever the loop publishes it."""
    await websocket.accept()
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            manager = current_manager()
            if manager is None:
                await _wait_for_rig(websocket)
                continue
            with manager.state_topic.subscribe() as queue:
                # A new client shouldn't wait a whole period to see anything.
                await websocket.send_json(rig_state(manager).model_dump(mode="json"))
                while current_manager() is manager:
                    try:
                        await asyncio.wait_for(queue.get(), IDLE_POLL_S)
                    except TimeoutError:
                        continue
                    await websocket.send_json(rig_state(manager).model_dump(mode="json"))


@router.websocket("/ws/readings")
async def readings(websocket: WebSocket) -> None:
    """Sensor readings only, for plotting."""
    await websocket.accept()
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            manager = current_manager()
            if manager is None:
                await _wait_for_rig(websocket)
                continue
            with manager.readings_topic.subscribe(maxsize=200) as queue:
                while current_manager() is manager:
                    try:
                        readings = await asyncio.wait_for(queue.get(), IDLE_POLL_S)
                    except TimeoutError:
                        continue
                    await websocket.send_json(readings_state(readings).model_dump(mode="json"))


@router.websocket("/ws/warnings")
async def warnings(websocket: WebSocket) -> None:
    """Faults the loop reported. A dropped frame is lost, so state carries them too."""
    await websocket.accept()
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            manager = current_manager()
            if manager is None:
                await _wait_for_rig(websocket)
                continue
            with manager.warnings_topic.subscribe(maxsize=50) as queue:
                while current_manager() is manager:
                    try:
                        message: Any = await asyncio.wait_for(queue.get(), IDLE_POLL_S)
                    except TimeoutError:
                        continue
                    await websocket.send_json(
                        {"time": message.time.seconds, "error": message.error}
                    )
