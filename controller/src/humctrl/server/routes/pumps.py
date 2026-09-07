from __future__ import annotations

from fastapi import APIRouter

from humctrl.pumps import Efforts, Flows, PumpsOutput, PumpsView
from humctrl.pumps.types import CurrentBlend
from humctrl.server.deps import ManagerDep, PumpsDep
from humctrl.server.schemas import (
    BlendRequest,
    EffortsRequest,
    FlowsRequest,
)
from humctrl.typing import Normalised

router = APIRouter(prefix="/api/pumps", tags=["pumps"])


@router.get("/")
async def read_view(pumps: PumpsDep) -> PumpsView | None:
    return pumps.view


@router.get("/flows")
async def read_flows(pumps: PumpsDep) -> Flows:
    """Each line independently, in absolute flow units."""
    return pumps.flows


@router.put("/flows")
async def set_flows(body: FlowsRequest, manager: ManagerDep) -> PumpsOutput:
    """Each line independently, in absolute flow units. Suspends the controller."""
    return manager.set_flows(body.wet, body.dry)


@router.get("/efforts")
async def read_efforts(pumps: PumpsDep) -> Efforts:
    """Each line independently, in normalised effort units."""
    return pumps.efforts


@router.put("/efforts")
async def set_efforts(body: EffortsRequest, manager: ManagerDep) -> PumpsOutput:
    """Each line independently, in normalised effort units. Suspends the controller."""
    return manager.set_efforts(body.wet, body.dry)


@router.get("/blend")
async def read_blend(pumps: PumpsDep) -> CurrentBlend:
    """Total flow and blend ratio together."""
    return pumps.blend


@router.put("/blend")
async def set_blend(body: BlendRequest, manager: ManagerDep) -> PumpsOutput:
    """Total flow and blend ratio together. Suspends the controller."""
    output = manager.set_blend(body.flow.parse(), body.wet_fraction)
    return output


@router.get("/wet-fraction")
async def read_wet_fraction(manager: ManagerDep) -> Normalised:
    return manager.require_pumps().wet_fraction


@router.get("/dry-fraction")
async def read_dry_fraction(manager: ManagerDep) -> Normalised:
    return manager.require_pumps().dry_fraction


@router.post("/stop")
async def stop(manager: ManagerDep) -> None:
    """Stop the pumps, leaving the loop running."""
    manager.stop_pumps()
