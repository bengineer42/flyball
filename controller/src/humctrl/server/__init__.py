"""HTTP + WebSocket front end for a running rig.

Wire a rig in, then serve::

    from humctrl.server import app, set_rig
    set_rig(rig)

    uvicorn humctrl.server:app --host 0.0.0.0 --port 8000
"""

from humctrl.server.app import app, create_app
from humctrl.server.deps import get_rig, set_rig

__all__ = ["app", "create_app", "get_rig", "set_rig"]
