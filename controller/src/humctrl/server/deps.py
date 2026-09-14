"""Rig and store injection.

The server owns no hardware and no database. Whatever builds them -- the
daemon, a simulation harness, a test -- calls :func:`set_rig` and
:func:`set_store` before serving, so the same app runs against a real
chamber, a simulated plant, or a database copied from another machine.
"""

from __future__ import annotations

from typing import Annotated, Any, Protocol

from fastapi import Depends, HTTPException

from humctrl.db import Store
from humctrl.runtime.rig import Rig

from .telemetry import Telemetry


class Programmer(Protocol):
    """What the program routes need of ``humctrl.programmer.Programmer``.

    A protocol rather than the class so the server does not import the
    programmer package to type a dependency.
    """

    @property
    def state(self) -> Any: ...
    def start(self, work: Any, interrupt: bool = False) -> None: ...
    def interrupt(self) -> None: ...


_rig: Rig | None = None
_telemetry: Telemetry | None = None
_store: Store | None = None
_programmer: Programmer | None = None


def set_rig(rig: Rig | None) -> None:
    """Attach a rig, and with it the observer that feeds the websockets."""
    global _rig, _telemetry
    if _rig is not None and _telemetry is not None:
        _rig.detach_observer(_telemetry)
    _rig, _telemetry = rig, None
    if rig is not None:
        _telemetry = Telemetry(rig)
        rig.attach_observer(_telemetry)


def current_telemetry() -> Telemetry | None:
    return _telemetry


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
