"""Routers the app mounts."""

from .actuators import router as actuators_router
from .events import router as events_router
from .history import router as history_router
from .library import router as library_router
from .program import router as program_router
from .readers import router as readers_router
from .recording import router as recording_router
from .rig import router as rig_router
from .schema import router as schema_router
from .signals import router as signals_router
from .telemetry import router as telemetry_router

__all__ = [
    "actuators_router",
    "events_router",
    "history_router",
    "library_router",
    "program_router",
    "readers_router",
    "recording_router",
    "rig_router",
    "schema_router",
    "signals_router",
    "telemetry_router",
]
