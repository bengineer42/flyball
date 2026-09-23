"""Rig and store injection.

The server owns no hardware and no database; whatever builds them calls
[set_rig][flyball.interfaces.server.deps.set_rig] and
[set_store][flyball.interfaces.server.deps.set_store] before serving.

The store is synchronous and one lock serialises it, so nothing on the event
loop may call it: a route that takes `StoreDep` is a plain `def` (FastAPI runs
it on its threadpool), or hands the store call to `anyio.to_thread`. `StoreDep`
also takes one of `STORE_SLOTS`' few tokens for the request, so requests queued
behind a long store call wait on the loop, not on threadpool threads the rest of
the API needs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Protocol

from anyio import CapacityLimiter
from fastapi import Depends, HTTPException

from flyball.model.catalog import Catalogs, current_catalog
from flyball.model.catalog import set_catalog as set_catalog
from flyball.record import Store
from flyball.rig import Rig

from .dialect import Dialect

if TYPE_CHECKING:
    from flyball.foundation.device import Device
    from flyball.runtime.config import Exposure, RigConfig, RunnerConfig
    from flyball.runtime.retention import Retention
    from flyball.sequencing import ProgrammerState


class Programmer(Protocol):
    """What the program routes need of `flyball.sequencing.Programmer`, without importing it."""

    @property
    def state(self) -> ProgrammerState: ...
    def start(self, work: Any, interrupt: bool = False) -> None: ...
    def interrupt(self) -> None: ...


class Simulation(Protocol):
    """What the sim routes and the runner need of `flyball_sim.simulation.Simulation`.

    A structural type, not an import: `flyball-sim` is a genuinely optional
    package (engine does not depend on it, even for type-checking), only
    built when a rig turns out to be fully simulated -- see
    `flyball.runner.main`.
    """

    @property
    def plants(self) -> dict[str, Any]: ...
    @property
    def speed(self) -> float: ...
    def describe(self) -> dict[str, Any]: ...
    def set_speed(self, speed: float) -> float: ...
    def step(self, seconds: float) -> int: ...
    def plant_config(self, name: str) -> Any: ...
    def plant_state(self, name: str) -> dict[str, Any]: ...
    def set_plant(self, name: str, **parameters: Any) -> Any: ...
    def reset_plant(
        self, name: str, output: float | None = None, input: float | None = None
    ) -> dict[str, Any]: ...
    def config_document(self) -> dict[str, Any]: ...
    def save(self, path: str | Path | None = None) -> Path: ...


_rig: Rig | None = None
_store: Store | None = None
_programmer: Programmer | None = None
_dialect: Dialect | None = None
_simulation: Simulation | None = None
_simulation_device: Device | None = None
_rig_config: RigConfig | None = None
_retention: Retention | None = None


def set_rig(rig: Rig | None) -> None:
    """Attach a rig; the websockets watch its `Latest` cells, so nothing else is wired."""
    global _rig
    _rig = rig


def current_rig() -> Rig | None:
    """The attached rig, or None. For lifespan and telemetry, which tolerate absence."""
    return _rig


def set_rig_config(config: RigConfig | None) -> None:
    """The config the rig was built from, for `/api/rig/config` on a hardware rig."""
    global _rig_config
    _rig_config = config


def current_rig_config() -> RigConfig | None:
    return _rig_config


def set_retention(retention: Retention | None) -> None:
    """The runner's sweeps over the store: the scratch record, rotation, retention, the cap."""
    global _retention
    _retention = retention


def current_retention() -> Retention | None:
    return _retention


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
    """The dialect an app set with [set_dialect][flyball.interfaces.server.deps.set_dialect].

    Falls back to a plain `Dialect` of every registered command and no
    modifiers, built from the attached catalog -- so a rig that never calls
    `set_dialect` still gets one, without building it eagerly at import time
    (the catalog is not set yet then).
    """
    if _dialect is not None:
        return _dialect
    return Dialect(commands=dict(get_catalog().commands.items()))


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
        _rig.add_device(device)  # its values live in the rig's router, like any device's
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


_drivers_dir: Path | None = None
_compose: bool = False


def set_compose(allowed: bool) -> None:
    """Whether a hardware rig may be built up over the API (`flyball-runner --compose`)."""
    global _compose
    _compose = allowed


def compose_allowed() -> bool:
    return _compose


class Runner(Protocol):
    """What the runner routes need of `flyball.runner.Handle`, without importing it."""

    @property
    def settings(self) -> RunnerConfig: ...
    @property
    def files(self) -> list[Path]: ...
    @property
    def exposure(self) -> Exposure | None: ...
    def shutdown(self) -> None: ...
    def restart(self) -> None: ...


_runner: Runner | None = None


def set_runner(runner: Runner | None) -> None:
    """The process serving this app, for `/api/runner`; None where nothing is (a test client)."""
    global _runner
    _runner = runner


def current_runner() -> Runner | None:
    return _runner


def current_exposure() -> dict[str, Any] | None:
    """Where the runner serves against where it was asked to; None where nothing is."""
    exposure = None if _runner is None else _runner.exposure
    return None if exposure is None else exposure.as_dict()


def save_allowed() -> bool:
    """Whether the API may write rig files (`flyball-runner --allow-save`)."""
    return _runner is not None and _runner.settings.allow_save


def set_drivers_dir(path: Path | None) -> None:
    """The directory `/api/drivers/reload` re-imports; None when the runner was given none."""
    global _drivers_dir
    _drivers_dir = path


def current_drivers_dir() -> Path | None:
    return _drivers_dir


def get_store() -> Store:
    if _store is None:
        raise HTTPException(status_code=503, detail="No store attached to this server")
    return _store


STORE_SLOTS = CapacityLimiter(4)
"""How many requests may be using the store at once.

The store serialises on one lock, so more in flight only queue for it -- and a
queued sync route holds one of anyio's 40 worker threads, shared with every
other sync route (demands, commands). Past this many, a store request waits
for a slot on the event loop, costing no thread.
"""


async def store_slot() -> AsyncIterator[Store]:
    """`get_store()`, holding one of `STORE_SLOTS` until the request is answered."""
    store = get_store()
    async with STORE_SLOTS:
        yield store


def get_catalog() -> Catalogs:
    """`current_catalog()`, or a 503: what `/api/drivers` and rig validation build from.

    `set_catalog`/`current_catalog` are `flyball.model.catalog`'s, re-exported
    here (not a second global) so a route can depend on them the same way it
    depends on `RigDep`/`current_drivers_dir` -- `runner.py` calls
    `set_catalog(Catalogs().discover())` once at startup, same lifetime as
    `set_rig`.
    """
    catalog = current_catalog()
    if catalog is None:
        raise HTTPException(status_code=503, detail="No catalog attached to this server")
    return catalog


RigDep = Annotated[Rig, Depends(get_rig)]
StoreDep = Annotated[Store, Depends(store_slot)]
ProgrammerDep = Annotated[Programmer, Depends(get_programmer)]
DialectDep = Annotated[Dialect, Depends(get_dialect)]
SimulationDep = Annotated[Simulation, Depends(get_simulation)]
SimulationDeviceDep = Annotated["Device", Depends(get_simulation_device)]
CatalogDep = Annotated[Catalogs, Depends(get_catalog)]
