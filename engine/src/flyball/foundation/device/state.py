"""What is true of a device now, and what happened: conditions and events."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class Level(IntEnum):
    """How much a condition or an event matters. `logging`'s numbers, so they interleave."""

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40


@dataclass(frozen=True, slots=True)
class Condition:
    """Something true of a device now: offline, railed, overdriven, waiting.

    In the device's state while it holds; a late-joining client sees the
    present, not a log.
    """

    kind: str  # stable and machine-readable: "offline", "railed"
    level: Level
    message: str
    since_ns: int


@dataclass(frozen=True, slots=True)
class Event:
    """Something that happened, for a log: a step failed, a pump clamped a request, a reader died.

    A [Condition][flyball.foundation.device.state.Condition] is what is true now and lives
    in state; an event is a point in time and lives in a stream and the
    session store.
    """

    time_ns: int
    level: Level
    scope: str
    """Which part: `loop`, `actuator`, `reader`, `program`, `rig`."""
    subject: str
    """The loop, device or step it concerns."""
    kind: str
    """Stable and machine-readable: `step_failed`, `offline`, `clamped`."""
    message: str
    details: Any = None
