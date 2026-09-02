"""Rig-level state: readings, set point, controller lifecycle, recording."""

from __future__ import annotations

from fastapi import APIRouter

from humctrl.server.deps import ManagerDep
from humctrl.server.schemas import (
    ControllerState,
    ReadingsState,
    ReadingState,
    RecordingRequest,
    RigState,
    SetPointRequest,
    StartControllerRequest,
)
from humctrl.server.snapshots import controller_state, rig_state
from humctrl.state import State, View

router = APIRouter(prefix="/api", tags=["rig"])


@router.get("/view")
async def read_root(manager: ManagerDep) -> View:
    return manager.view


@router.get("/state")
async def read_state(manager: ManagerDep) -> State:
    return manager.state


@router.get("/readings")
async def read_readings(manager: ManagerDep) -> ReadingsState:
    """The last readings the loop took. Does not touch the sensors."""
    return manager.readings


@router.get("/process_reading")
async def read_process_reading(manager: ManagerDep) -> ReadingState:
    # The loop owns the sensors; this reports what it last saw rather than
    # doing blocking I2C on the event loop.
    return ReadingState.of(manager.required_process_reading)


# region Controller


@router.get("/controller")
async def read_controller(manager: ManagerDep) -> ControllerState | None:
    return controller_state(manager)


@router.post("/controller")
async def start_controller(body: StartControllerRequest, manager: ManagerDep) -> ControllerState:
    """Start regulating. Omit ``control_law`` to run open loop."""
    flow = None if body.flow is None else body.flow.to_blend_flow()
    if body.control_law is None:
        manager.start_open_loop_controller(humidity=body.humidity, flow=flow)
    else:
        manager.start_controller(humidity=body.humidity, flow=flow, control_law=body.control_law)
    return controller_state(manager)  # type: ignore[return-value]


@router.put("/controller/set_point")
async def set_set_point(body: SetPointRequest, manager: ManagerDep) -> ControllerState:
    manager.update_target(body.humidity)
    return controller_state(manager)  # type: ignore[return-value]


@router.post("/controller/resume")
async def resume_controller(manager: ManagerDep) -> ControllerState:
    """Hand the pumps back after manual control, without stepping the output."""
    manager.resume_controller()
    return controller_state(manager)  # type: ignore[return-value]


@router.get("/set_point")
async def read_set_point(manager: ManagerDep) -> float | None:
    return manager.set_point


# endregion

# region Recording


@router.post("/recording/start")
async def start_recording(body: RecordingRequest, manager: ManagerDep) -> RigState:
    manager.start_recording(body.flag)
    return rig_state(manager)


@router.post("/recording/stop")
async def stop_recording(body: RecordingRequest, manager: ManagerDep) -> RigState:
    manager.stop_recording(body.flag)
    return rig_state(manager)


@router.post("/recording/flag")
async def add_flag(body: RecordingRequest, manager: ManagerDep) -> None:
    if body.flag is not None:
        manager.add_recorder_flag(body.flag)


# endregion


@router.post("/stop")
async def stop(manager: ManagerDep) -> RigState:
    """Stop the loop and the pumps."""
    manager.stop()
    return rig_state(manager)
