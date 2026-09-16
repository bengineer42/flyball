"""Live push over websockets.

Every socket sends what the rig knows on connect, then every `FLUSH_S` one
frame of whatever changed: the rig keeps only the newest value per key in
a [Latest][flyball.core.topic.Latest] cell -- samples by node address,
write states by signal address, controller states and device runs by name,
waits by name -- so a socket costs at most one frame per flush at any tick
rate, and an idle server builds nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter

from flyball.core.signal import Sample
from flyball.core.topic import Latest
from flyball.runtime.polling import DeviceRun
from flyball.runtime.rig import Rig
from flyball.runtime.triggers import TriggerState
from flyball.server.deps import current_rig
from flyball.server.schemas import ControllerOut, SampleOut, WriteOut

router = APIRouter(tags=["telemetry"])

# How long a socket waits for a push before checking whether the rig went away.
IDLE_POLL_S = 1.0
FLUSH_S = 0.05
"""How often a socket sends what changed; a dashboard needs at most ~20/s."""

RUN = TypeAdapter(DeviceRun)
WAIT = TypeAdapter(TriggerState)


async def _no_rig(websocket: WebSocket) -> None:
    await websocket.send_json({"error": "no rig attached"})
    await asyncio.sleep(IDLE_POLL_S)


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


def _sample_out(rig: Rig, node: str, sample: Any) -> dict[str, Any]:
    return SampleOut.of(sample).model_dump(mode="json")


def _write_out(rig: Rig, address: str, state: Any) -> dict[str, Any]:
    return {"signal": address, **WriteOut.of(state).model_dump(mode="json")}


def _controller_out(rig: Rig, name: str, state: Any) -> dict[str, Any]:
    """The tick's state joined to the settings, under the lock: a retune swaps the law."""
    with rig.lock:
        controller = rig.controllers[name]
        out = ControllerOut.of(controller, name == rig.controllers.default, state)
    return out.model_dump(mode="json")


def _run_out(rig: Rig, name: str, run: Any) -> dict[str, Any]:
    return {"name": name, **RUN.dump_python(run, mode="json")}


def _wait_out(rig: Rig, name: str, state: Any) -> dict[str, Any]:
    return WAIT.dump_python(state, mode="json")


def _prime(rig: Rig) -> None:
    """Seed the cells so a new client's first frame has everything, not just what ticks next."""
    with rig.lock:
        for name, controller in rig.controllers.items():
            rig.controller_states.set(name, controller.state)
        for device in rig.devices.values():
            for signal, state in device.written.items():
                rig.write_states.set(signal.address, state)
            known = rig.read(device.root)
            for sample in [known] if isinstance(known, Sample) else known:
                if (published := sample.published()) is not None:
                    rig.samples.set(published.node.address, published)
        for name in rig.polling.by_name:
            rig.polling.runs.set(name, rig.polling.run(name))


async def _serve[V](
    websocket: WebSocket,
    cell: Callable[[Rig], Latest[str, V]],
    key: str,
    encode: Callable[[Rig, str, V], dict[str, Any]],
) -> None:
    await websocket.accept()
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            rig = current_rig()
            if rig is None:
                await _no_rig(websocket)
                continue
            _prime(rig)
            await _flush(websocket, cell(rig), key, encode)


@router.websocket("/ws/samples")
async def samples(websocket: WebSocket) -> None:
    """The newest published sample per node on connect, then each node that delivered.

    Only what publishes: a fresh read of a setting is not here. At most one
    sample per node per flush; a chart at a higher rate reads history.
    """
    await _serve(websocket, lambda rig: rig.samples, "samples", _sample_out)


@router.websocket("/ws/writes")
async def writes(websocket: WebSocket) -> None:
    """Every write state on connect, then each signal committed, at most every `FLUSH_S`."""
    await _serve(websocket, lambda rig: rig.write_states, "writes", _write_out)


@router.websocket("/ws/controllers")
async def controllers(websocket: WebSocket) -> None:
    """Every controller on connect, then each that ticked, at most every `FLUSH_S`."""
    await _serve(websocket, lambda rig: rig.controller_states, "controllers", _controller_out)


@router.websocket("/ws/devices")
async def devices(websocket: WebSocket) -> None:
    """Every polled device's run on connect, then each as it reads, fails or is restarted."""
    await _serve(websocket, lambda rig: rig.polling.runs, "devices", _run_out)


@router.websocket("/ws/waits")
async def waits(websocket: WebSocket) -> None:
    """Every registered wait on connect, then each as it is registered or settles."""
    await _serve(websocket, lambda rig: rig.triggers.latest, "waits", _wait_out)
