from __future__ import annotations

from fastapi import APIRouter

from humctrl.pumps import Efforts, Flows, PumpsOutput, PumpsView
from humctrl.pumps.types import CurrentBlend
from humctrl.server.deps import PumpsDep, RigDep
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
async def set_flows(body: FlowsRequest, rig: RigDep) -> PumpsOutput:
    """Each line independently, in absolute flow units. Suspends the controller."""
    return rig.set_flows(body.wet, body.dry)


@router.get("/efforts")
async def read_efforts(pumps: PumpsDep) -> Efforts:
    """Each line independently, in normalised effort units."""
    return pumps.efforts


@router.put("/efforts")
async def set_efforts(body: EffortsRequest, rig: RigDep) -> PumpsOutput:
    """Each line independently, in normalised effort units. Suspends the controller."""
    return rig.set_efforts(body.wet, body.dry)


@router.get("/blend")
async def read_blend(pumps: PumpsDep) -> CurrentBlend:
    """Total flow and blend ratio together."""
    return pumps.blend


@router.put("/blend")
async def set_blend(body: BlendRequest, rig: RigDep) -> PumpsOutput:
    """Total flow and blend ratio together. Suspends the controller."""
    output = rig.set_blend(body.flow.parse(), body.wet_fraction)
    return output


@router.get("/wet-fraction")
async def read_wet_fraction(rig: RigDep) -> Normalised:
    return rig.require_pumps().wet_fraction


@router.get("/dry-fraction")
async def read_dry_fraction(rig: RigDep) -> Normalised:
    return rig.require_pumps().dry_fraction


@router.post("/stop")
async def stop(rig: RigDep) -> None:
    """Stop the pumps, leaving the loop running."""
    rig.stop_pumps()
