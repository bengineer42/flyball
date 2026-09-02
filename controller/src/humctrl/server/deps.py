"""Rig injection.

The server owns no hardware. Whatever builds the rig — ``humctrl.daemon``, a
simulation harness, a test — calls :func:`set_manager` before serving, so the
same app runs against a real chamber, a simulated plant or a stub.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException

from humctrl.controller import Controller
from humctrl.manager import Manager
from humctrl.pumps import DualPumps

_manager: Manager | None = None


def set_manager(manager: Manager | None) -> None:
    global _manager
    _manager = manager


def current_manager() -> Manager | None:
    """The attached rig, or None. For lifespan and telemetry, which tolerate absence."""
    return _manager


def get_manager() -> Manager:
    if _manager is None:
        raise HTTPException(status_code=503, detail="No rig attached to this server")
    return _manager


def get_pumps() -> DualPumps:
    manager = get_manager()
    if manager._pumps is None:
        raise HTTPException(status_code=503, detail="No pumps attached to this rig")
    return manager._pumps


def get_controller() -> Controller:
    manager = get_manager()
    if manager.controller is None:
        raise HTTPException(status_code=503, detail="No controller attached to this rig")
    return manager.controller


ManagerDep = Annotated[Manager, Depends(get_manager)]
PumpsDep = Annotated[DualPumps, Depends(get_pumps)]
ControllerDep = Annotated[Controller, Depends(get_controller)]
