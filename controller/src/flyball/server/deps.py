"""Rig and store injection.

The server owns no hardware and no database; whatever builds them calls
[set_rig][flyball.server.deps.set_rig] and
[set_store][flyball.server.deps.set_store] before serving.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Protocol

from fastapi import Depends, HTTPException

from flyball.db import Store
from flyball.runtime.rig import Rig

from .dialect import Dialect

if TYPE_CHECKING:
    from flyball.core.device import Device
    from flyball.programmer import ProgrammerState
    from flyball.runtime.simulation import Simulation


class Programmer(Protocol):
    """What the program routes need of `flyball.programmer.Programmer`, without importing it."""

    @property
    def state(self) -> ProgrammerState: ...
    def start(self, work: Any, interrupt: bool = False) -> None: ...
    def interrupt(self) -> None: ...


_rig: Rig | None = None
_store: Store | None = None
_programmer: Programmer | None = None
_dialect: Dialect = Dialect()
_simulation: Simulation | None = None
_simulation_device: Device | None = None


def set_rig(rig: Rig | None) -> None:
    """Attach a rig; the websockets watch its `Latest` cells, so nothing else is wired."""
    global _rig
    _rig = rig


def current_rig() -> Rig | None:
    """The attached rig, or None. For lifespan and telemetry, which tolerate absence."""
    return _rig


def get_rig() -> Rig:
    if _rig is None:
        raise HTTPException(status_code=503, detail="No rig attached to this server")
    return _rig


def set_programmer(programmer: Programmer | None) -> None:
    """Attach the programmer that runs commands against the rig."""
    global _programmer
    _programmer = programmer


def get_programmer() -> Programmer:
    if _programmer is None:
        raise HTTPException(status_code=503, detail="No programmer attached to this server")
    return _programmer


def set_dialect(dialect: Dialect) -> None:
    """The program-file dialect the server reads and publishes the schema of."""
    global _dialect
    _dialect = dialect


def get_dialect() -> Dialect:
    return _dialect


def set_simulation(simulation: Simulation | None) -> None:
    """Attach the knobs of a simulated rig; None for a rig with real hardware."""
    global _simulation
    _simulation = simulation


def get_simulation() -> Simulation:
    if _simulation is None:
        raise HTTPException(status_code=409, detail="This rig is not a simulation")
    return _simulation


def current_simulation() -> Simulation | None:
    return _simulation


def set_simulation_device(device: Device | None) -> None:
    """Attach the device an application puts its simulation-only knobs on: `/api/sim/device`.

    Claims its name in the attached rig's device namespace too -- it shows up
    in `GET /api/devices` beside the rig's own devices, and cannot share a
    name with one. ConflictError if it does.
    """
    global _simulation_device
    if _simulation_device is not None and _rig is not None:
        _rig.release(_simulation_device.name)
    if device is not None and _rig is not None:
        _rig.claim(device.name, "simulation", device)
        _rig.devices[device.name] = device
    _simulation_device = device


def get_simulation_device() -> Device:
    if _simulation_device is None:
        raise HTTPException(status_code=404, detail="This simulation has no device of its own")
    return _simulation_device


def current_simulation_device() -> Device | None:
    return _simulation_device


_programs_dir: Path | None = None


def set_programs_dir(directory: Path | None) -> None:
    """Where program files live on disk; the library imports them on start and on request."""
    global _programs_dir
    _programs_dir = directory


def current_programs_dir() -> Path | None:
    return _programs_dir


def set_store(store: Store | None) -> None:
    global _store
    _store = store


def get_store() -> Store:
    if _store is None:
        raise HTTPException(status_code=503, detail="No store attached to this server")
    return _store


RigDep = Annotated[Rig, Depends(get_rig)]
StoreDep = Annotated[Store, Depends(get_store)]
ProgrammerDep = Annotated[Programmer, Depends(get_programmer)]
DialectDep = Annotated[Dialect, Depends(get_dialect)]
SimulationDep = Annotated["Simulation", Depends(get_simulation)]
SimulationDeviceDep = Annotated["Device", Depends(get_simulation_device)]
