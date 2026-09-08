from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import SerializeAsAny

from humctrl.controller import ControlLawConfig, ControlLawView, Tuning
from humctrl.error import TuningNotRegisteredError
from humctrl.readers import Reading, Readings
from humctrl.server.deps import RigDep
from humctrl.state import State, View
from humctrl.utils import require

from ..schemas import DefaultTuningRequest, LawConfig, LawsSchema
from .controller import router as controller_router
from .program import command_router
from .pumps import router as pumps_router
from .telemetry import router as telemetry_router

rig_router = APIRouter(prefix="/api", tags=["rig"])
router = rig_router

__all__ = [
    "command_router",
    "controller_router",
    "pumps_router",
    "rig_router",
    "telemetry_router",
]


@router.get("/")
async def read_root(rig: RigDep) -> View:
    return rig.view


@router.get("/state")
async def read_state(rig: RigDep) -> State:
    return rig.state


@router.get("/readings")
async def read_readings(rig: RigDep) -> Readings:
    """The last readings the loop took. Does not touch the sensors."""
    return rig.readings


@router.get("/reading/process")
async def read_process_reading(rig: RigDep) -> Reading | None:
    return rig.process_sensor_reading


@router.get("/reading/dry")
async def read_dry_reading(rig: RigDep) -> Reading | None:
    # The loop owns the sensors; this reports what it last saw rather than
    # doing blocking I2C on the event loop.
    return rig.dry_sensor_reading


@router.get("/reading/wet")
async def read_wet_reading(rig: RigDep) -> Reading | None:
    return rig.wet_sensor_reading


# region Controller


@router.get("/laws/schema")
async def read_law_schema() -> dict[str, Any]:
    """The JSON schema for every registered control law, for building a form."""
    return LawsSchema


@router.get("/tunings")
async def read_tunings(
    rig: RigDep,
) -> dict[str, SerializeAsAny[ControlLawConfig | ControlLawView]]:
    return rig.tunings


@router.get("/tuning")
async def read_default_tuning(rig: RigDep) -> Tuning | None:
    return rig.default_tuning


@router.put("/tuning")
async def set_default_tuning(body: DefaultTuningRequest, rig: RigDep) -> Tuning:
    """Point the rig's default tuning at an already stored one.

    Args:
        body: Names the tuning to make default.
        rig: The attached rig.

    Returns:
        The tuning now in use as the default.
    """
    return rig.set_default_tuning(body.tag)


@router.get("/tuning/{name}")
async def read_tuning(
    rig: RigDep, name: str
) -> SerializeAsAny[ControlLawConfig | ControlLawView]:
    return rig.require_tuning(name)


@router.put("/tuning/{name}")
async def set_tuning(body: LawConfig, rig: RigDep, name: str, default: bool = False) -> Tuning:
    """Store ``body`` under ``name``, replacing any tuning already there.

    Args:
        body: The law config to store, validated against the law its tag names.
        rig: The attached rig.
        name: What to store it under.
        default: ``?default=true`` also makes it the rig's default tuning. A
            query parameter, not a field: which tuning is the default is the
            rig's configuration, not part of the tuning itself.

    Returns:
        The tuning as stored.
    """
    rig.add_tuning(tag=name, config=body, default=default)
    return Tuning(tag=name, config=body)


@router.delete("/tuning/{name}")
async def delete_tuning(rig: RigDep, name: str) -> Tuning:
    return require(rig.remove_tuning(name), TuningNotRegisteredError, name)


# endregion


@router.post("/stop")
async def stop(rig: RigDep) -> State:
    """Stop the loop and the pumps."""
    rig.stop()
    return rig.state
