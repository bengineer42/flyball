"""A client for a running rig, built from the schema it publishes.

See [flyball.interfaces.client.rig][].
"""

from .rig import Device, Devices, Rig, RigError, Unreachable, segment
from .validate import SchemaError, validate

__all__ = [
    "Device",
    "Devices",
    "Rig",
    "RigError",
    "SchemaError",
    "Unreachable",
    "segment",
    "validate",
]
