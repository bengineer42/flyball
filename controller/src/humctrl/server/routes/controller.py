from __future__ import annotations

from fastapi import APIRouter

from humctrl.controller import (
    ControlLawConfig,
    ControlLawState,
    ControlLawView,
    ControllerState,
    ControllerView,
)
from humctrl.server.deps import ControllerDep, RigDep
from humctrl.server.schemas import StartControllerRequest
from humctrl.state import ControllerOutput
from humctrl.typing import UnclampedPercent

router = APIRouter(prefix="/api/controller", tags=["controller"])


@router.get("/")
async def read_view(controller: ControllerDep) -> ControllerView | None:
    return controller.view


@router.post("/")
async def create_controller(body: StartControllerRequest, rig: RigDep) -> ControllerOutput:
    return rig.start_controller(*body.parse())


@router.get("/state")
async def read_state(controller: ControllerDep) -> ControllerState | None:
    return controller.state


@router.get("/spec")
async def read_spec(controller: ControllerDep) -> ControlLawConfig | None:
    return controller.law.config


@router.get("/law")
async def read_law(controller: ControllerDep) -> ControlLawView | None:
    return controller.law.view


@router.get("/law/config")
async def read_config(controller: ControllerDep) -> ControlLawConfig | None:
    return controller.law.config


@router.get("/law/state")
async def read_law_state(controller: ControllerDep) -> ControlLawState | None:
    return controller.law.state


@router.get("/suspended")
async def read_suspended(controller: ControllerDep) -> bool:
    return controller.suspended


@router.get("/set-point")
async def read_setpoint(controller: ControllerDep) -> UnclampedPercent | None:
    return controller.setpoint


@router.get("/demand")
async def read_demand(controller: ControllerDep) -> UnclampedPercent | None:
    return controller.demand


@router.get("/correction")
async def read_correction(controller: ControllerDep) -> UnclampedPercent | None:
    return controller.correction


# @router.put("/suspend")
# async def suspend_controller(controller: ControllerDep) -> None:
#     controller.suspend()


@router.put("/resume")
async def resume_controller(rig: RigDep) -> ControllerOutput:
    return rig.resume_controller()
