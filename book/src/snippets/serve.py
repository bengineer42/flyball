"""Serve the simulated oven: `python serve.py`, then `flyball actuators` in another shell."""

import uvicorn

from flyball.core import Clock
from flyball.server import app, set_rig

from oven import build

rig = build(Clock(), period=1.0)  # real time: the probe is polled every second
rig.loops["heater"].regulate(100.0)

set_rig(rig)
uvicorn.run(app, host="127.0.0.1", port=8000)
