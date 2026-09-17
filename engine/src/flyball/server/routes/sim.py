"""A simulated rig's knobs: clock speed, plant parameters, and saving them to the rig file.

Mounted for every rig; each route answers 409 when the rig has real
hardware, so a client can tell a simulation from a rig by asking.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from flyball.core.device import Device
from flyball.server.deps import (
    RigDep,
    SimulationDep,
    SimulationDeviceDep,
    current_simulation,
    current_simulation_device,
    save_allowed,
)

from .devices import device_schema, run

router = APIRouter(prefix="/api/sim", tags=["sim"])


class SpeedIn(BaseModel):
    speed: float = Field(gt=0, description="Rig seconds per wall second.")


class StepIn(BaseModel):
    seconds: float = Field(gt=0)


class ResetIn(BaseModel):
    output: float | None = None
    input: float | None = None


class SaveIn(BaseModel):
    path: str | None = Field(
        default=None, description="Where; default: where the rig was loaded from."
    )


@router.get("")
async def read_simulation() -> dict[str, Any]:
    """The clock, every plant with its config and state, and what has changed since the last save.

    `{"simulated": false}` for a rig with real hardware. `device` says
    whether `/api/sim/device` exists, so a client need not probe it and get
    a 404 on every simulated rig that has no application device of its own.
    """
    simulation = current_simulation()
    if simulation is None:
        return {"simulated": False}
    return {
        "simulated": True,
        "device": current_simulation_device() is not None,
        **simulation.describe(),
    }


@router.put("/clock")
def set_clock(body: SpeedIn, simulation: SimulationDep) -> dict[str, Any]:
    """Run the rig's time at `speed` times wall time from now on."""
    return {"speed": simulation.set_speed(body.speed)}


@router.post("/clock/step")
def step_clock(body: StepIn, simulation: SimulationDep) -> dict[str, Any]:
    """Advance a stepped clock by `seconds`; 409 if the clock runs on its own."""
    return {"now_ns": simulation.step(body.seconds)}


@router.get("/plants/{name}")
async def read_plant(name: str, simulation: SimulationDep) -> dict[str, Any]:
    return {
        "config": simulation.plant_config(name).model_dump(mode="json"),
        **simulation.plant_state(name),
    }


@router.put("/plants/{name}")
def set_plant(name: str, parameters: dict[str, Any], simulation: SimulationDep) -> dict[str, Any]:
    """Change some of a plant's parameters while it runs: `{"tau_s": 30, "noise": 0.2}`."""
    return simulation.set_plant(name, **parameters).model_dump(mode="json")


@router.post("/plants/{name}/reset")
def reset_plant(name: str, body: ResetIn, simulation: SimulationDep) -> dict[str, float]:
    """Put the plant at an output and/or input, at once."""
    return simulation.reset_plant(name, body.output, body.input)


@router.get("/config")
async def read_config(simulation: SimulationDep) -> dict[str, Any]:
    """The rig file as it now stands, with every change applied."""
    return simulation.config_document()


@router.post("/save")
def save_config(simulation: SimulationDep, body: SaveIn | None = None) -> dict[str, str]:
    """Write the current config to the rig file (or `path`), in the format its suffix names.

    409 unless the runner was started with `--allow-save`: this rewrites a file.
    """
    if not save_allowed():
        raise HTTPException(
            status_code=409,
            detail="Saving needs the runner started with --allow-save (runner.allow_save)",
        )
    return {"path": str(simulation.save(body.path if body is not None else None))}


# region The application's own device
#
# What a simulation can change that a rig file cannot name -- the humidity
# chamber's time constant, its sensors' noise -- an application exposes as one
# [Device][flyball.core.device.Device] attached with
# [set_simulation_device][flyball.server.deps.set_simulation_device]. The
# same three routes as a device's, so the same panel renders it. 404 when
# the simulation has none, so a client can hide it.


@router.get("/device")
def read_device(rig: RigDep, device: SimulationDeviceDep) -> Any:
    """The device's config, and its signals' current values."""
    assert isinstance(device, Device)
    return {
        "config": device.config,
        "values": {
            path: None if (r := rig.router.reading(s)) is None else r.value
            for path, s in device.signals.items()
        },
    }


@router.get("/device/schema")
def read_device_schema(device: SimulationDeviceDep) -> dict[str, Any]:
    return device_schema(device)


@router.post("/device/{command}")
def run_device_command(
    rig: RigDep,
    device: SimulationDeviceDep,
    command: str,
    body: Annotated[dict[str, Any] | None, Body()] = None,
) -> Any:
    """Run the command with the validated body; respond with whatever it returns."""
    assert isinstance(device, Device)
    return run(rig, device, command, body)


# endregion
