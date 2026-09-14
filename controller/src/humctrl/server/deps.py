"""Rig and store injection.

The server owns no hardware and no database. Whatever builds them -- the
daemon, a simulation harness, a test -- calls :func:`set_rig` and
:func:`set_store` before serving, so the same app runs against a real
chamber, a simulated plant, or a database copied from another machine.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException

from humctrl.db import Store
from humctrl.runtime.rig import Rig

_rig: Rig | None = None
_store: Store | None = None


def set_rig(rig: Rig | None) -> None:
    global _rig
    _rig = rig


def current_rig() -> Rig | None:
    """The attached rig, or None. For lifespan and telemetry, which tolerate absence."""
    return _rig


def get_rig() -> Rig:
    if _rig is None:
        raise HTTPException(status_code=503, detail="No rig attached to this server")
    return _rig


def set_store(store: Store | None) -> None:
    global _store
    _store = store


def get_store() -> Store:
    if _store is None:
        raise HTTPException(status_code=503, detail="No store attached to this server")
    return _store


RigDep = Annotated[Rig, Depends(get_rig)]
StoreDep = Annotated[Store, Depends(get_store)]
