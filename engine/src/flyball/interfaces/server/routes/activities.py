"""What the rig is waiting on, and answering it.

A program step that waits registers a named activity: a prompt, a timed wait,
a settle test, a ramp's end. These routes list them, fire one (answer a
prompt, or skip what the step waits on) or interrupt one (stop the program
here).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import TypeAdapter

from flyball.interfaces.server.deps import RigDep
from flyball.rig import TriggerState

router = APIRouter(prefix="/api/activities", tags=["activities"])

STATE = TypeAdapter(TriggerState)


@router.get("")
def read_activities(rig: RigDep) -> dict[str, Any]:
    """Every registered activity by name: pending, or settled but not yet taken down."""
    return {
        name: STATE.dump_python(state, mode="json") for name, state in rig.triggers.states().items()
    }


@router.get("/{name}")
def read_activity(rig: RigDep, name: str) -> Any:
    return STATE.dump_python(rig.triggers.state(name), mode="json")


@router.post("/{name}/fire")
def fire_activity(rig: RigDep, name: str) -> dict[str, Any]:
    """Settle the activity as met. False if it had already settled."""
    return {"name": name, "fired": rig.triggers.fire(name)}


@router.post("/{name}/interrupt")
def interrupt_activity(rig: RigDep, name: str) -> dict[str, Any]:
    """Cancel the activity; the program stops at this step."""
    return {"name": name, "interrupted": rig.triggers.interrupt(name)}
