"""Routers the app mounts.

The program router is written against the current programmer but is not
exported until ``flyball.programmer`` imports again; see ``program.py``.
"""

from .history import router as history_router
from .rig import router as rig_router
from .telemetry import router as telemetry_router

__all__ = ["history_router", "rig_router", "telemetry_router"]
