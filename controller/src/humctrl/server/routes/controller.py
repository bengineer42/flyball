from __future__ import annotations

from fastapi import APIRouter

from humctrl.controller.types import ControlLawConfig, ControllerState, ControllerView
from humctrl.server.deps import ControllerDep

router = APIRouter(prefix="/api/controller", tags=["controller"])


@router.get("/")
async def read_view(controller: ControllerDep) -> ControllerView | None:
    return controller.view


@router.get("/state")
async def read_state(controller: ControllerDep) -> ControllerState | None:
    return controller.state


@router.get("/spec")
async def read_spec(controller: ControllerDep) -> str | ControlLawConfig | None:
    return controller.spec


@router.put("/suspend")
async def suspend_controller(controller: ControllerDep) -> None:
    controller.suspend()
