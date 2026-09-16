"""What the rig is waiting on, and answering it.

A waiting program step registers a named signal. These routes list them, fire
one (answer or skip the wait) or interrupt one (stop the program here).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import TypeAdapter

from flyball.runtime.triggers import TriggerState
from flyball.server.deps import RigDep

router = APIRouter(prefix="/api/signals", tags=["signals"])

STATE = TypeAdapter(TriggerState)


@router.get("")
def read_signals(rig: RigDep) -> dict[str, Any]:
    """Every registered signal by name: pending, or settled but not yet taken down."""
    return {
        name: STATE.dump_python(state, mode="json") for name, state in rig.triggers.states().items()
    }


@router.get("/{name}")
def read_signal(rig: RigDep, name: str) -> Any:
    return STATE.dump_python(rig.triggers.state(name), mode="json")


@router.post("/{name}/fire")
def fire_signal(rig: RigDep, name: str) -> dict[str, Any]:
    """Settle the wait as met. False if it had already settled."""
    return {"name": name, "fired": rig.triggers.fire(name)}


@router.post("/{name}/interrupt")
def interrupt_signal(rig: RigDep, name: str) -> dict[str, Any]:
    """Cancel the wait; the program stops at this step."""
    return {"name": name, "interrupted": rig.triggers.interrupt(name)}
