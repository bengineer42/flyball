"""Live state push.

Subscribes to the rig's own topics rather than keeping a parallel one, so
the server and the control loop cannot disagree about what was published.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter

from humctrl.readers import HTReadings
from humctrl.rig import ErrorMsg, Rig
from humctrl.server.deps import current_rig
from humctrl.state import State
from humctrl.utils import Topic

router = APIRouter(tags=["telemetry"])

# How long a socket waits for a push before checking whether the rig went away.
IDLE_POLL_S = 1.0


async def _wait_for_rig(websocket: WebSocket) -> Rig | None:
    """Tell the client there is no rig, then let it keep the socket open."""
    await websocket.send_json({"error": "no rig attached"})
    await asyncio.sleep(IDLE_POLL_S)
    return current_rig()


async def push[T](
    websocket: WebSocket,
    adapter: TypeAdapter[T],
    topic: Callable[[Rig], Topic[T]],
    maxsize: int = 1,
    snapshot: Callable[[Rig], T] | None = None,
) -> None:
    """Push everything ``topic`` publishes, until the rig goes away.

    ``T`` ties the three together, so the state adapter cannot be paired with
    the readings topic. It is inferred from the arguments: a type parameter does
    not exist at runtime, so the adapter has to be passed as a value.

    Args:
        websocket: The already-unaccepted socket to serve.
        adapter: Turns a published value into a JSON-safe dict.
        topic: The rig's topic to subscribe to.
        maxsize: How many published items to hold before dropping the oldest.
        snapshot: The value to send on connecting, so a new client does not wait
            a whole period to see anything. Omit where there is nothing to show.
    """
    await websocket.accept()
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            rig = current_rig()
            if rig is None:
                await _wait_for_rig(websocket)
                continue
            with topic(rig).subscribe(maxsize=maxsize) as queue:
                if snapshot is not None:
                    await websocket.send_json(adapter.dump_python(snapshot(rig), mode="json"))
                while current_rig() is rig:
                    try:
                        published = await asyncio.wait_for(queue.get(), IDLE_POLL_S)
                    except TimeoutError:
                        continue
                    # Send what was published, not a fresh read: the loop may
                    # have stepped again since, and the client would skip a frame.
                    await websocket.send_json(adapter.dump_python(published, mode="json"))


STATE = TypeAdapter(State)


@router.websocket("/ws/telemetry")
async def telemetry(websocket: WebSocket) -> None:
    """Full rig state, pushed whenever the loop publishes it."""
    await push(websocket, STATE, lambda rig: rig.state_topic, snapshot=lambda m: m.state)


READINGS = TypeAdapter(HTReadings)


@router.websocket("/ws/readings")
async def readings(websocket: WebSocket) -> None:
    """Sensor readings only, for plotting."""
    await push(websocket, READINGS, lambda rig: rig.readings_topic, maxsize=200)


@router.websocket("/ws/warnings")
async def warnings(websocket: WebSocket) -> None:
    """Faults the loop reported. A dropped frame is lost, so state carries them too.

    Kept separate from :func:`push`: the payload is built by hand rather than
    dumped from a model, so only the reconnect loop would be shared.
    """
    await websocket.accept()
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            rig = current_rig()
            if rig is None:
                await _wait_for_rig(websocket)
                continue
            with rig.warnings_topic.subscribe(maxsize=50) as queue:
                while current_rig() is rig:
                    try:
                        message: ErrorMsg = await asyncio.wait_for(queue.get(), IDLE_POLL_S)
                    except TimeoutError:
                        continue
                    await websocket.send_json({
                        "time": message.time.seconds,
                        "detail": message.detail,
                    })
