"""Pieces for a rig with no hardware.

Nothing here knows what is simulated; an application composes these into its
own simulator. `runtime` must never import this package.
"""

from .clock import SteppedClock
from .plant import Lag
from .reader import FunctionReader
from .sink import RecordingActuator

__all__ = ["FunctionReader", "Lag", "RecordingActuator", "SteppedClock"]
