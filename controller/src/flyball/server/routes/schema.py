"""Everything a client needs to build itself, in one request."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from flyball.server.deps import RigDep

from .devices import actuator_schema, device_schema, source_schema

router = APIRouter(prefix="/api/schema", tags=["schema"])


@router.get("")
def read_schema(rig: RigDep) -> dict[str, Any]:
    """Every actuator's and reader's schema, by name. A CLI or a client builds from this."""
    actuators: dict[str, Any] = {}
    for name, actuator in rig.actuators.items():
        actuators[name] = actuator_schema(actuator)
    return {
        "actuators": actuators,
        "readers": {
            name: device_schema(r, sources=[source_schema(s) for s in r.sources])
            for name, r in rig.readers.by_name.items()
        },
    }
