from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import SerializeAsAny

from humctrl.controller import ControlLawConfig, ControlLawView, Tuning
from humctrl.error import TuningNotRegisteredError
from humctrl.readers import Reading, Readings
from humctrl.server.deps import ManagerDep
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
async def read_root(manager: ManagerDep) -> View:
    return manager.view


@router.get("/state")
async def read_state(manager: ManagerDep) -> State:
    return manager.state


@router.get("/readings")
async def read_readings(manager: ManagerDep) -> Readings:
    """The last readings the loop took. Does not touch the sensors."""
    return manager.readings


@router.get("/reading/process")
async def read_process_reading(manager: ManagerDep) -> Reading | None:
    return manager.process_sensor_reading


@router.get("/reading/dry")
async def read_dry_reading(manager: ManagerDep) -> Reading | None:
    # The loop owns the sensors; this reports what it last saw rather than
    # doing blocking I2C on the event loop.
    return manager.dry_sensor_reading


@router.get("/reading/wet")
async def read_wet_reading(manager: ManagerDep) -> Reading | None:
    return manager.wet_sensor_reading


# region Controller


@router.get("/laws/schema")
async def read_law_schema() -> dict[str, Any]:
    """The JSON schema for every registered control law, for building a form."""
    return LawsSchema


@router.get("/tunings")
async def read_tunings(
    manager: ManagerDep,
) -> dict[str, SerializeAsAny[ControlLawConfig | ControlLawView]]:
    return manager.tunings


@router.get("/tuning")
async def read_default_tuning(manager: ManagerDep) -> Tuning | None:
    return manager.default_tuning


@router.put("/tuning")
async def set_default_tuning(body: DefaultTuningRequest, manager: ManagerDep) -> Tuning:
    """Point the rig's default tuning at an already stored one.

    Args:
        body: Names the tuning to make default.
        manager: The attached rig.

    Returns:
        The tuning now in use as the default.
    """
    return manager.set_default_tuning(body.tag)


@router.get("/tuning/{name}")
async def read_tuning(
    manager: ManagerDep, name: str
) -> SerializeAsAny[ControlLawConfig | ControlLawView]:
    return manager.require_tuning(name)


@router.put("/tuning/{name}")
async def set_tuning(
    body: LawConfig, manager: ManagerDep, name: str, default: bool = False
) -> Tuning:
    """Store ``body`` under ``name``, replacing any tuning already there.

    Args:
        body: The law config to store, validated against the law its tag names.
        manager: The attached rig.
        name: What to store it under.
        default: ``?default=true`` also makes it the rig's default tuning. A
            query parameter, not a field: which tuning is the default is the
            manager's configuration, not part of the tuning itself.

    Returns:
        The tuning as stored.
    """
    manager.add_tuning(tag=name, config=body, default=default)
    return Tuning(tag=name, config=body)


@router.delete("/tuning/{name}")
async def delete_tuning(manager: ManagerDep, name: str) -> Tuning:
    return require(manager.remove_tuning(name), TuningNotRegisteredError, name)


# endregion


@router.post("/stop")
async def stop(manager: ManagerDep) -> State:
    """Stop the loop and the pumps."""
    manager.stop()
    return manager.state
