"""Actuators: what each one is, and the commands its class marked.

Routes resolve against the live rig at request time, since the app exists
before the rig is set. OpenAPI therefore lists one generic command route;
`/{name}/schema` carries each command's real request schema.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body

from flyball.control.loop import LoopMode
from flyball.core.errors import ConflictError, NotFoundError
from flyball.core.sink import Actuator
from flyball.server.deps import RigDep

from .devices import actuator_schema, run

router = APIRouter(prefix="/api/actuators", tags=["actuators"])


def _actuator(rig: RigDep, name: str) -> Actuator:
    try:
        return rig.actuators[name]
    except KeyError as e:
        raise NotFoundError(f"Actuator {name!r} not found") from e


@router.get("")
def read_actuators(rig: RigDep) -> dict[str, Any]:
    """Every attached actuator's view, by name."""
    return {name: actuator.view for name, actuator in rig.actuators.items()}


@router.get("/{name}")
def read_actuator(rig: RigDep, name: str) -> Any:
    return _actuator(rig, name).view


@router.get("/{name}/schema")
def read_actuator_schema(rig: RigDep, name: str) -> dict[str, Any]:
    return actuator_schema(_actuator(rig, name))


# Plain `def`: FastAPI runs it in the threadpool, so a command that touches
# hardware never blocks the event loop.
@router.post("/{name}/{command}")
def run_command(
    rig: RigDep, name: str, command: str, body: Annotated[dict[str, Any] | None, Body()] = None
) -> Any:
    """Call the marked method with the validated body; respond with whatever it returns."""
    actuator = _actuator(rig, name)
    if command == "demand" and name in rig.loops and rig.loops[name].mode is LoopMode.REGULATING:
        raise ConflictError(
            f"{name!r} is being regulated by its loop; stop the loop to drive it by hand"
        )
    result = run(actuator, command, body)
    rig.apply(actuator)
    return result
