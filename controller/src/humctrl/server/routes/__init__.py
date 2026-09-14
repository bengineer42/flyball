"""Routers the app mounts.

Only routers written against the current rig and store are exported. The
controller, pumps, program and telemetry routes predate ``runtime.rig`` and
are rewired as those parts land.
"""

from .history import router as history_router
from .rig import router as rig_router

__all__ = ["history_router", "rig_router"]
