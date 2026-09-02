"""HTTP + WebSocket front end for a running rig.

Wire a rig in, then serve::

    from humctrl.server import app, set_manager
    set_manager(manager)

    uvicorn humctrl.server:app --host 0.0.0.0 --port 8000
"""

from humctrl.server.app import app, create_app
from humctrl.server.deps import get_manager, set_manager

__all__ = ["app", "create_app", "get_manager", "set_manager"]
