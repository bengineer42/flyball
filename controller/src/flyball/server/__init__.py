"""HTTP + WebSocket front end for a running rig.

Wire a rig in, then serve::

    from flyball.server import app, set_rig
    set_rig(rig)

    uvicorn flyball.server:app --host 0.0.0.0 --port 8000
"""

from flyball.server.app import app, create_app
from flyball.server.deps import get_rig, set_rig

__all__ = ["app", "create_app", "get_rig", "set_rig"]
