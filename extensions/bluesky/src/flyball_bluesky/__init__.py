"""Run flyball devices inside a Bluesky plan.

Wraps a node's readings or one writable signal in Bluesky's duck-typed
*Readable* and *Movable* shapes without importing bluesky, so the `bluesky`
extra is only needed to run a plan:

    from bluesky import RunEngine
    from bluesky.plans import count
    RE = RunEngine()
    RE(count([NodeReadable(rig, rig.resolve("hum_sensors.dry"))], num=10))
"""

from ._adapter import NodeReadable, SignalMovable, Status

__all__ = ["NodeReadable", "SignalMovable", "Status"]
