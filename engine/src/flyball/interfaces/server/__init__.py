"""HTTP + WebSocket front end for a running rig.

from flyball.interfaces.server import app, set_rig
set_rig(rig)

uvicorn flyball.interfaces.server:app --host 127.0.0.1 --port 8000

That `app` has no door: serve it on loopback only. `flyball-runner` (and
[serve][flyball.runner.serving.serve]) is what refuses an open runner beyond it.
"""

from flyball.interfaces.server.app import app, create_app
from flyball.interfaces.server.deps import (
    get_rig,
    set_dialect,
    set_programmer,
    set_rig,
    set_simulation,
)

__all__ = [
    "app",
    "create_app",
    "get_rig",
    "set_dialect",
    "set_programmer",
    "set_rig",
    "set_simulation",
]
