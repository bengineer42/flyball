"""Serve the simulated oven: `python serve.py`, then `flyball devices` in another shell."""

import uvicorn

from flyball.foundation import Clock
from flyball.interfaces.server import app, set_rig

from oven import build

rig = build(Clock(), period=1.0)  # real time: the probe is polled every second
rig.start_polling(rig.devices["probe"])
rig.controllers.resolve(None).regulate(100.0)

set_rig(rig)
uvicorn.run(app, host="127.0.0.1", port=8000)
