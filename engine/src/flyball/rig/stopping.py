"""The stop boundary's types: who asked for a stop, and what it did.

Here rather than in `flyball.runner.stopping` so the server's stop route can name
them: the server sits below the runner and may not import it. `flyball.runner.stopping`
re-exports them beside the break-glass. What a stop does to outputs is not decided
here; whatever implements [Stopper][flyball.rig.stopping.Stopper] decides it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

__all__ = ["Actor", "DeviceStop", "StopReport", "Stopper"]


@dataclass(frozen=True)
class Actor:
    """Who asked for a stop: the principal's `sub`/`sid`/`kind`, and the way it came in."""

    sub: str
    sid: str
    kind: str
    via: Literal["http", "mcp", "signal"]
    detail: str = ""
    """Anything more about the caller, e.g. `signal from pid 4121 uid 1000`."""


class DeviceStop(TypedDict):
    """What a stop did to one device."""

    state: Literal["stopped", "held", "failed"]
    detail: str


@dataclass(frozen=True)
class StopReport:
    """What one stop did, device by device."""

    at_ns: int
    actor: Actor
    reason: str
    devices: dict[str, DeviceStop]
    program_interrupted: bool
    controllers_manual: list[str]
    interim: bool
    """True until the real stop (the signals work's) replaces the interim one."""


class Stopper(Protocol):
    """Stops the rig. Thread-safe; one device failing is reported, never raised."""

    def stop(self, actor: Actor, reason: str) -> StopReport: ...
