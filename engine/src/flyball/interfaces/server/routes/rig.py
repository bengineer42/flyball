"""The live rig: one look at what it is doing, its clock, and its tunings.

Read-only except for tunings; nothing here touches hardware. What the rig is
made of is under `/api/devices` and `/api/controllers`.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import SerializeAsAny, ValidationError

from flyball.control.errors import TuningNotRegisteredError
from flyball.foundation.device import Condition, Device
from flyball.interfaces.server.deps import (
    RigDep,
    current_rig,
    current_rig_config,
    current_simulation,
)
from flyball.interfaces.server.schemas import ClockOut, LawConfig
from flyball.library.tunings import Tuning
from flyball.model.law import ControlLawConfig, ControlLawView
from flyball.rig import Rig
from flyball.runtime.config import RigConfig, canonical, rig_schema

router = APIRouter(prefix="/api", tags=["rig"])

# A tuning body is any registered law's config, told apart by its tag --
# every built-in law's, direct, same as `interfaces.server.schemas.LawConfig`
# (reused here rather than rebuilt): see that module's comment on why this
# isn't `get_catalog()`/`Catalogs.discover()`.


def _outside(value: float, band: tuple[float, float] | None) -> bool:
    return band is not None and not (band[0] <= value <= band[1])


def _alarm_summary(rig: Rig, conditions: list[dict[str, Any]]) -> dict[str, int]:
    """Amber and red counts: signals outside their `warn`/`alarm` bands, plus device conditions.

    A signal already outside `alarm` is not also counted in `warn`: the
    chip shows the worse of the two. A device condition at `WARNING` (30)
    or above counts the same way, by its own level.
    """
    warn = alarm = 0
    with rig.lock:
        latest = list(rig.latest.items())
    for signal, reading in latest:
        if _outside(reading.value, signal.spec.alarm):
            alarm += 1
        elif _outside(reading.value, signal.spec.warn):
            warn += 1
    for c in conditions:
        if c["level"] >= 40:
            alarm += 1
        elif c["level"] >= 30:
            warn += 1
    return {
        "warn": warn,
        "alarm": alarm,
        "max_level": 40 if alarm else 30 if warn else 0,
    }


def _device_conditions(rig: Rig, device: Device) -> tuple[Condition, ...]:
    reading = rig.router.reading(device.conditions)
    return () if reading is None else tuple(reading.value)


def _conditions(rig: Rig) -> list[dict[str, Any]]:
    """What every device reports of itself, then what the runtime knows: polling, writing."""
    return (
        [
            {"device": name, **asdict(c)}
            for name, device in rig.devices.items()
            for c in _device_conditions(rig, device)
        ]
        + [
            {"device": name, **asdict(c)}
            for name in rig.polling.by_name
            for c in rig.polling.run(name).conditions
        ]
        + [{"device": name, **asdict(c)} for name, c in rig.write_conditions()]
    )


# region Health


@router.get("/health")
async def read_health() -> dict[str, Any]:
    """One look: is anything offline, slow or pending. What a watchdog or a status line polls."""
    rig = current_rig()
    if rig is None:
        return {"ok": False, "rig": None}
    conditions = _conditions(rig)
    return {
        "ok": not any(c["level"] >= 40 for c in conditions),
        "rig": rig.name,
        "uptime_s": rig.clock.elapsed_s(),
        "devices": {
            name: {"running": run.running, "last_read_ns": run.last_read_ns}
            for name, run in ((n, rig.polling.run(n)) for n in rig.polling.by_name)
        },
        "controllers": {name: c.mode.value for name, c in rig.controllers.items()},
        "conditions": conditions,
        "alarms": _alarm_summary(rig, conditions),
        "waits": sorted(rig.triggers.states()),
        "recording": rig.recording is not None,
    }


# endregion

# region Clock


@router.get("/clock")
async def read_clock(rig: RigDep) -> ClockOut:
    """The rig's timebase now: start, elapsed, and the instant this was answered."""
    return ClockOut.of(rig.clock)


# endregion

# region Rig file


@router.get("/rig/schema")
async def read_rig_schema() -> dict[str, Any]:
    """The rig file's JSON schema, with every driver this runner has installed."""
    return rig_schema()


@router.get("/rig/config")
async def read_rig_config() -> dict[str, Any]:
    """The rig file as it now stands: a simulation's with its changes, else what was loaded."""
    if (simulation := current_simulation()) is not None:
        return simulation.config_document()
    if (config := current_rig_config()) is None:
        raise HTTPException(status_code=404, detail="This server was not started from a rig file")
    return canonical(config)


@router.post("/rig/check")
async def check_rig(document: dict[str, Any]) -> dict[str, Any]:
    """Validate a rig document against the drivers installed here; nothing is built or run.

    Returns the canonical form. 422 says what is wrong. One document, not a
    layered set: merge layers and apply a board before sending.
    """
    try:
        config = RigConfig.model_validate(document)
    except (ValidationError, TypeError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return canonical(config)


# endregion

# region Tunings


@router.get("/tunings")
async def read_tunings(rig: RigDep) -> dict[str, SerializeAsAny[ControlLawConfig | ControlLawView]]:
    return rig.tunings.all()


@router.get("/tunings/{tag}")
async def read_tuning(rig: RigDep, tag: str) -> SerializeAsAny[ControlLawConfig | ControlLawView]:
    if (tuning := rig.tunings.get(tag)) is None:
        raise TuningNotRegisteredError(tag)
    return tuning


@router.put("/tunings/{tag}")
def set_tuning(rig: RigDep, tag: str, body: LawConfig) -> Tuning:  # type: ignore[valid-type]
    """Store `body` under `tag` on the live rig, replacing any tuning already there."""
    tuning = Tuning(tag=tag, config=body)
    with rig.lock:
        rig.tunings.add(tuning)
    return tuning


# endregion
