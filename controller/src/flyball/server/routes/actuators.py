"""Actuators: what each one is, and the commands its class marked.

Routes are resolved at request time against the live rig rather than mounted
per actuator, since the app exists before the rig is set. The price is that
OpenAPI lists one generic command route; ``/{name}/schema`` carries the real
request schema for each command, which is what a form or CLI reads anyway.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body

from flyball.core.errors import NotFoundError
from flyball.core.sink import Actuator
from flyball.server.deps import RigDep

from .devices import device_schema, run

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
    actuator = _actuator(rig, name)
    unit = type(actuator).demand_unit
    return device_schema(actuator, demand_unit=None if unit is None else unit.symbol)


# Plain ``def``: FastAPI runs it in the threadpool, so a command that touches
# hardware never blocks the event loop.
@router.post("/{name}/{command}")
def run_command(
    rig: RigDep, name: str, command: str, body: Annotated[dict[str, Any] | None, Body()] = None
) -> Any:
    """Call the marked method with the validated body; respond with whatever it returns."""
    actuator = _actuator(rig, name)
    result = run(actuator, command, body)
    rig.apply(actuator)
    return result
