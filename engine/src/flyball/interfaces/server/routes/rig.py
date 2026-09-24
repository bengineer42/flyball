"""The live rig: one look at what it is doing, its clock, and its tunings.

Read-only except for tunings; nothing here touches hardware. What the rig is
made of is under `/api/devices` and `/api/controllers`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import SerializeAsAny, TypeAdapter, ValidationError

from flyball.control.errors import TuningNotRegisteredError
from flyball.foundation.device import Code, Condition, Severity, SubjectKind
from flyball.interfaces.server.deps import (
    RigDep,
    current_exposure,
    current_rig,
    current_rig_config,
    current_simulation,
)
from flyball.interfaces.server.redact import without_credentials
from flyball.interfaces.server.schemas import ClockOut, LawConfig
from flyball.library.tunings import Tuning
from flyball.model.law import ControlLawConfig, ControlLawView
from flyball.rig import Rig
from flyball.runtime.config import RigConfig, canonical, rig_schema

router = APIRouter(prefix="/api", tags=["rig"])

# A tuning body is any registered law's config, told apart by its type --
# every built-in law's, direct, same as `interfaces.server.schemas.LawConfig`
# (reused here rather than rebuilt): see that module's comment on why this
# isn't `get_catalog()`/`Catalogs.discover()`.


BANDS = frozenset({Code.BAND_WARNING, Code.BAND_ALARM, Code.BAND_UNKNOWN})
"""The conditions that are alarms, not faults: a reading outside a band, or a band unknown."""

_RANK: dict[str, int] = {Code.BAND_WARNING: 1, Code.BAND_ALARM: 2, Code.BAND_UNKNOWN: 3}
"""Which a signal counts under when caught holding two: `unknown` over `alarm` over `warn`."""


def _alarm_summary(conditions: list[dict[str, Any]]) -> dict[str, int]:
    """Signals holding `band_warning` (`warn`), `band_alarm` (`alarm`), `band_unknown` (`unknown`).

    Counted from the rig's band conditions, so the count keeps the rig's
    hysteresis; a fault condition (offline, write_failed) is never an alarm.
    Each signal counts once: one with no value counts in `unknown` only,
    whatever band it held before; one caught mid-swap counts as `alarm`.
    `max_level` is 40 with any `alarm`, 30 with only `warn`, else 0:
    `unknown` does not raise it.
    """
    held: dict[str, str] = {}
    for c in conditions:
        if c["subject_kind"] != SubjectKind.SIGNAL or c["code"] not in BANDS:
            continue
        if _RANK[c["code"]] > _RANK.get(held.get(c["subject"], ""), 0):
            held[c["subject"]] = c["code"]
    alarm = sum(1 for code in held.values() if code == Code.BAND_ALARM)
    unknown = sum(1 for code in held.values() if code == Code.BAND_UNKNOWN)
    warn = len(held) - alarm - unknown
    return {
        "warn": warn,
        "alarm": alarm,
        "unknown": unknown,
        "max_level": 40 if alarm else 30 if warn else 0,
    }


CONDITION = TypeAdapter(Condition)


def _conditions(rig: Rig) -> list[dict[str, Any]]:
    """Every condition held now, from the rig's store: the runtime's and the drivers' alike.

    Each carries its `subject_kind` and `subject`. A snapshot of the store under its
    own lock, never the rig's: the health route runs on the event loop, which
    must not wait on a delivery.
    """
    return [CONDITION.dump_python(c, mode="json") for c in rig.conditions.all()]


# region Health


@router.get("/health")
async def read_health() -> dict[str, Any]:
    """One look: is anything offline, slow or pending. What a watchdog or a status line polls.

    `ok` is false with any fault condition at `error`; a band alarm is not a
    fault, and is counted in `alarms` instead. `stopped` is the rig stop's latch
    (`{by, at_ns, reason}`, null when not stopped); `latches` every latch cause held,
    one row per subject (`{subject_kind, subject, cause}`).

    Lock-free: on the event loop, a watchdog must be answered while a delivery holds the
    rig's lock, so it reads C-level `list(...)` snapshots of the rig's dicts instead.
    """
    rig = current_rig()
    if rig is None:
        return {
            "ok": False,
            "rig": None,
            "stopped": None,
            "latches": [],
            "exposure": current_exposure(),
        }
    conditions = _conditions(rig)
    stopped = rig.stopping.latches.rig_stop
    return {
        "ok": not any(
            c["severity"] == Severity.ERROR and c["code"] not in BANDS for c in conditions
        ),
        "rig": rig.name,
        "uptime_s": rig.clock.elapsed_s(),
        "devices": {
            name: {"running": run.running, "last_read_ns": run.last_read_ns}
            for name, run in rig.polling.snapshot().items()
        },
        "controllers": {name: c.mode.value for name, c in list(rig.controllers.items())},
        "conditions": conditions,
        "alarms": _alarm_summary(conditions),
        "activities": sorted(rig.triggers.states()),
        "recording": rig.recording is not None,
        "stopped": None
        if stopped is None
        else {"by": stopped.by, "at_ns": stopped.at_ns, "reason": stopped.reason},
        "latches": rig.stopping.latches.rows(),
        "exposure": current_exposure(),  # served on loopback though asked for more, or open
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
    """The rig file as it now stands: a simulation's with its changes, else what was loaded.

    Of `runner:`, only what a reader may see (`redact.PUBLIC_RUNNER_KEYS`): no credential, no path.
    """
    if (simulation := current_simulation()) is not None:
        return without_credentials(simulation.config_document())
    if (config := current_rig_config()) is None:
        raise HTTPException(status_code=404, detail="This server was not started from a rig file")
    return without_credentials(canonical(config))


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


@router.get("/tunings/{name}")
async def read_tuning(rig: RigDep, name: str) -> SerializeAsAny[ControlLawConfig | ControlLawView]:
    if (tuning := rig.tunings.get(name)) is None:
        raise TuningNotRegisteredError(name)
    return tuning


@router.put("/tunings/{name}")
def set_tuning(rig: RigDep, name: str, body: LawConfig) -> Tuning:  # type: ignore[valid-type]
    """Store `body` under `name` on the live rig, replacing any tuning already there."""
    tuning = Tuning(name=name, config=body)
    with rig.lock:
        rig.tunings.add(tuning)
    return tuning


# endregion
