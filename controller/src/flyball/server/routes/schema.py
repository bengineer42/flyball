"""Everything a client needs to build itself, in one request."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from flyball.server.deps import RigDep

from .devices import device_schema

router = APIRouter(prefix="/api/schema", tags=["schema"])


@router.get("")
def read_schema(rig: RigDep) -> dict[str, Any]:
    """Every device's schema, by name. A CLI or a client builds from this."""
    return {"devices": {name: device_schema(device) for name, device in rig.devices.items()}}
