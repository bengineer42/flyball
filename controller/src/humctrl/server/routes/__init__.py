from humctrl.server.routes.pumps import router as pumps_router
from humctrl.server.routes.rig import router as rig_router
from humctrl.server.routes.telemetry import router as telemetry_router

__all__ = ["pumps_router", "rig_router", "telemetry_router"]
