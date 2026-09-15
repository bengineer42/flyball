"""Pieces for a rig with no hardware.

Nothing here knows what is being simulated. An application composes these
into its own simulator -- a chamber, a heater, a stage -- and the library's
tests use them directly. ``runtime`` must never import this package.
"""

from .clock import SteppedClock
from .plant import Lag
from .reader import FunctionReader
from .sink import RecordingActuator

__all__ = ["FunctionReader", "Lag", "RecordingActuator", "SteppedClock"]
