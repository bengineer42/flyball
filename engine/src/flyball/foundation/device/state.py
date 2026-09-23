"""What is true of a device now, and what happened: conditions and events."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    """How much a condition or an event matters: a lowercase string on the wire and in the store.

    Ordered by [rank][flyball.foundation.device.state.Severity.rank], which is
    `logging`'s number, so a log line and an event interleave.
    """

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @property
    def rank(self) -> int:
        """`logging`'s level: 10, 20, 30, 40. Compare severities by this, not as strings."""
        return _RANKS[self]


_RANKS = {Severity.DEBUG: 10, Severity.INFO: 20, Severity.WARNING: 30, Severity.ERROR: 40}


class Scope(StrEnum):
    """Which part of the rig an event concerns; its `subject` names which one."""

    DEVICE = "device"
    CONTROLLER = "controller"
    PROGRAM = "program"
    RIG = "rig"


class Code(StrEnum):
    """What the engine itself reports, as a condition or an event: stable and machine-readable.

    A string on the wire and in the store. A driver's own conditions may use
    any string (the sim's `broken`); what the runtime raises is one of these,
    so a typo is a type error rather than a code nobody filters on.
    """

    # A device: its reads, its deliveries, its writes.
    OFFLINE = "offline"
    RESTARTED = "restarted"
    SLOW = "slow"
    DELIVERY_FAILED = "delivery_failed"
    WRITE_FAILED = "write_failed"
    WRITE_RECOVERED = "write_recovered"
    COMMIT_FAILED = "commit_failed"
    COMMIT_RECOVERED = "commit_recovered"
    DEMAND_IGNORED = "demand_ignored"
    # A controller.
    STEP_FAILED = "step_failed"
    STEP_RECOVERED = "step_recovered"
    STALE_INPUT = "stale_input"
    LIMIT_UNKNOWN = "limit_unknown"
    LIMIT_KNOWN = "limit_known"
    INTERRUPTED = "interrupted"
    # A program (`step_failed` too). It ends `succeeded`, `failed`, `cancelled` by a person,
    # or `interrupted` by the engine (a stop, a shutdown), with the reason.
    STARTED = "started"
    STEP = "step"
    STEP_TIMED_OUT = "step_timed_out"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RUN_FROM_LIBRARY = "run_from_library"
    # The rig.
    RECORDING_FAILED = "recording_failed"
    RESTORED = "restored"


@dataclass(frozen=True, slots=True)
class Condition:
    """Something true of a device now: offline, railed, overdriven, waiting.

    In the device's state while it holds; a late-joining client sees the
    present, not a log.
    """

    code: str
    """Stable and machine-readable: a [Code][flyball.foundation.device.state.Code] when the
    runtime raises it (`offline`, `slow`), any string a driver chooses for its own."""
    severity: Severity
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
    severity: Severity
    scope: str
    """Which part: a [Scope][flyball.foundation.device.state.Scope] -- `device`, `controller`,
    `program`, `rig`."""
    subject: str
    """The device, controller, program step or rig part it concerns."""
    code: str
    """Stable and machine-readable: a [Code][flyball.foundation.device.state.Code] --
    `step_failed`, `offline`. A plain string once read back from the store."""
    message: str
    details: Any = None
