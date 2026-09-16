"""Routers the app mounts."""

from .composition import router as composition_router
from .controllers import router as controllers_router
from .dashboards import router as dashboards_router
from .devices import router as devices_router
from .events import router as events_router
from .export import router as export_router
from .history import router as history_router
from .library import router as library_router
from .program import router as program_router
from .read import router as read_router
from .recording import router as recording_router
from .rig import router as rig_router
from .schema import router as schema_router
from .sim import router as sim_router
from .telemetry import router as telemetry_router
from .waits import router as waits_router

__all__ = [
    "composition_router",
    "controllers_router",
    "dashboards_router",
    "devices_router",
    "events_router",
    "export_router",
    "history_router",
    "library_router",
    "program_router",
    "read_router",
    "recording_router",
    "rig_router",
    "schema_router",
    "sim_router",
    "telemetry_router",
    "waits_router",
]
