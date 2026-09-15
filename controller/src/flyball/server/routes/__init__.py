"""Routers the app mounts. The program router is not exported yet; see `program.py`."""

from .actuators import router as actuators_router
from .history import router as history_router
from .readers import router as readers_router
from .rig import router as rig_router
from .schema import router as schema_router
from .signals import router as signals_router
from .telemetry import router as telemetry_router

__all__ = [
    "actuators_router",
    "history_router",
    "readers_router",
    "rig_router",
    "schema_router",
    "signals_router",
    "telemetry_router",
]
