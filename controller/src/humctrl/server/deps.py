"""Rig injection.

The server owns no hardware. Whatever builds the rig — ``humctrl.daemon``, a
simulation harness, a test — calls :func:`set_rig` before serving, so the
same app runs against a real chamber, a simulated plant or a stub.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException

from humctrl.control import Controller
from humctrl.pumps import DualPumps
from humctrl.rig import HumRig

_rig: HumRig | None = None


def set_rig(rig: HumRig | None) -> None:
    global _rig
    _rig = rig


def current_rig() -> HumRig | None:
    """The attached rig, or None. For lifespan and telemetry, which tolerate absence."""
    return _rig


def get_rig() -> HumRig:
    if _rig is None:
        raise HTTPException(status_code=503, detail="No rig attached to this server")
    return _rig


def get_pumps() -> DualPumps:
    return get_rig().require_pumps()


def get_controller() -> Controller:
    return get_rig().controller


RigDep = Annotated[HumRig, Depends(get_rig)]
PumpsDep = Annotated[DualPumps, Depends(get_pumps)]
ControllerDep = Annotated[Controller, Depends(get_controller)]
