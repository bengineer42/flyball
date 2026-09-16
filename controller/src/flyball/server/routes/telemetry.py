"""Live push over websockets.

`/ws/samples` forwards every sample as it arrives. `/ws/loops`,
`/ws/actuators` and `/ws/signals` send everything on connect, then every
`FLUSH_S` one frame of whatever changed: the rig keeps only the newest value
per key, so the socket costs at most one frame per flush at any tick rate.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter

from flyball.control import LoopView
from flyball.core.topic import Latest
from flyball.runtime.reader import ReaderRun
from flyball.runtime.rig import Rig
from flyball.runtime.triggers import TriggerState
from flyball.server.deps import current_rig, current_telemetry
from flyball.server.schemas import LoopOut

router = APIRouter(tags=["telemetry"])

# How long a socket waits for a push before checking whether the rig went away.
IDLE_POLL_S = 1.0
FLUSH_S = 0.05
"""How often the loop and actuator sockets send what changed; a dashboard needs at most ~20/s."""

SAMPLE = TypeAdapter(dict)
LOOPS = TypeAdapter(list[LoopOut])
ANY = TypeAdapter(Any)


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
            closed = asyncio.ensure_future(_closed(websocket))
            with telemetry.samples.subscribe(maxsize=200) as queue:
                getter: asyncio.Future[Any] = asyncio.ensure_future(queue.get())
                try:
                    while current_telemetry() is telemetry:
                        done, _ = await asyncio.wait(
                            {closed, getter},
                            timeout=IDLE_POLL_S,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if closed in done:
                            raise WebSocketDisconnect
                        if getter in done:
                            await websocket.send_json(getter.result().model_dump(mode="json"))
                            getter = asyncio.ensure_future(queue.get())
                finally:
                    getter.cancel()
                    closed.cancel()


async def _closed(websocket: WebSocket) -> None:
    """Return once the client has gone. Only receiving notices a disconnect."""
    while (await websocket.receive())["type"] != "websocket.disconnect":
        pass


async def _flush[V](
    websocket: WebSocket,
    latest: Latest[str, V],
    key: str,
    encode: Callable[[Rig, str, V], dict[str, Any]],
) -> None:
    """Send everything, then every `FLUSH_S` whatever changed, until the client or rig goes.

    Frames are `{key: [encoded, ...]}`; an empty flush sends nothing.
    """
    rig = current_rig()
    if rig is None:
        await _no_rig(websocket)
        return
    closed = asyncio.ensure_future(_closed(websocket))
    version = 0
    try:
        with latest.watch():
            while current_rig() is rig:
                version, changed = latest.changed_since(version)
                if changed:
                    await websocket.send_json({
                        key: [encode(rig, k, v) for k, v in changed.items()]
                    })
                done, _ = await asyncio.wait({closed}, timeout=FLUSH_S)
                if closed in done:
                    raise WebSocketDisconnect
    finally:
        closed.cancel()


def _loop_out(rig: Rig, name: str, state: Any) -> dict[str, Any]:
    """Join the tick's state to the loop's settings. Under the lock: a retune swaps the law."""
    with rig.lock:
        channel, loop = next((ch, lp) for ch, lp in rig.loops.entries() if lp.name == name)
        view = LoopView.of(loop.settings, state)
    return LoopOut.of_view(
        channel, view, name == rig.loops.default, loop.actuator.label
    ).model_dump(mode="json")


def _actuator_out(rig: Rig, name: str, state: Any) -> dict[str, Any]:
    return {"name": name, "state": ANY.dump_python(state, mode="json")}


SIGNAL = TypeAdapter(TriggerState)
READER_RUN = TypeAdapter(ReaderRun)


def _reader_out(rig: Rig, name: str, run: Any) -> dict[str, Any]:
    return {"name": name, **READER_RUN.dump_python(run, mode="json")}


def _signal_out(rig: Rig, name: str, state: Any) -> dict[str, Any]:
    return SIGNAL.dump_python(state, mode="json")


def _prime(rig: Rig) -> None:
    """Seed the cells so a new client's first frame has everything, not just what ticks next."""
    with rig.lock:
        for _, loop in rig.loops.entries():
            rig.loop_states.set(loop.name, loop.state)
        for name, actuator in rig.actuators.items():
            rig.actuator_states.set(name, actuator.state)
        for name in rig.readers.by_name:
            rig.readers.runs.set(name, rig.readers.run(name))


@router.websocket("/ws/loops")
async def loops(websocket: WebSocket) -> None:
    """Every loop on connect, then each loop that ticked, at most every `FLUSH_S`."""
    await websocket.accept()
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            rig = current_rig()
            if rig is None:
                await _no_rig(websocket)
                continue
            _prime(rig)
            await _flush(websocket, rig.loop_states, "loops", _loop_out)


@router.websocket("/ws/actuators")
async def actuators(websocket: WebSocket) -> None:
    """Every actuator on connect, then each that changed, at most every `FLUSH_S`."""
    await websocket.accept()
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            rig = current_rig()
            if rig is None:
                await _no_rig(websocket)
                continue
            _prime(rig)
            await _flush(websocket, rig.actuator_states, "actuators", _actuator_out)


@router.websocket("/ws/signals")
async def signals(websocket: WebSocket) -> None:
    """Every registered signal on connect, then each as it is registered or settles."""
    await websocket.accept()
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            rig = current_rig()
            if rig is None:
                await _no_rig(websocket)
                continue
            await _flush(websocket, rig.triggers.latest, "signals", _signal_out)


@router.websocket("/ws/readers")
async def readers(websocket: WebSocket) -> None:
    """Every reader's run on connect, then each as it reads, fails or is restarted."""
    await websocket.accept()
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            rig = current_rig()
            if rig is None:
                await _no_rig(websocket)
                continue
            _prime(rig)
            await _flush(websocket, rig.readers.runs, "readers", _reader_out)
