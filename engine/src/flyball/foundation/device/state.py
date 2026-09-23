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
    """Which part of the rig an event or a condition concerns; its `subject` names which one."""

    DEVICE = "device"
    SIGNAL = "signal"
    CONTROLLER = "controller"
    PROGRAM = "program"
    RIG = "rig"


class Edge(StrEnum):
    """Which way a condition went, on the event that records it; a point event has none."""

    RAISED = "raised"
    """Absent to present: the condition began."""
    CLEARED = "cleared"
    """Present to absent: it ended; the event's `details.duration_s` says how long it held."""


class Code(StrEnum):
    """What the engine itself reports, as a condition or an event: stable and machine-readable.

    A string on the wire and in the store. A driver's own conditions may use
    any string (the sim's `broken`); what the runtime raises is one of these,
    so a typo is a type error rather than a code nobody filters on. A
    condition's code is on both of its edges: `offline` raised, `offline`
    cleared -- there is no separate "recovered" code.
    """

    # A device: its reads, its deliveries, its writes.
    OFFLINE = "offline"
    """A condition: its last read raised; polling stopped until a restart clears it."""
    SLOW = "slow"
    """A condition: its reads take longer than its period (de-flapped: see polling)."""
    DELIVERY_FAILED = "delivery_failed"
    WRITE_FAILED = "write_failed"
    """A condition: a blocking device's writer failed its last write; cleared by the next that
    succeeds."""
    COMMIT_FAILED = "commit_failed"
    """A condition: a commit on the delivery path raised; cleared by the next that succeeds."""
    DEMAND_IGNORED = "demand_ignored"
    # A controller.
    STEP_FAILED = "step_failed"
    """A condition on a controller whose law raised; cleared when it steps again. A point event
    of a program's step, too."""
    STALE_INPUT = "stale_input"
    """A condition: the controller's measured signal is older than `stale_after_s`; held."""
    LIMIT_UNKNOWN = "limit_unknown"
    """A condition: a limit on the controller's output is not known; held."""
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
    """A condition on the rig: the recorder stopped on a store error; cleared by the next
    recording that starts."""
    RESTORED = "restored"
    RESTARTED = "restarted"
    """The runner's own, when it is restarted (not yet raised). A device's polling that starts
    again clears its `offline` instead."""


@dataclass(frozen=True, slots=True)
class Condition:
    """Something true now of a device, a signal, a controller or the rig: offline, slow, held.

    Held in the rig's condition store while it lasts, keyed by its owner
    object and its code; a late-joining client sees the present, not a log.
    Its start and its end are events (`raised`, `cleared`).
    """

    code: str
    """Stable and machine-readable: a [Code][flyball.foundation.device.state.Code] when the
    runtime raises it (`offline`, `slow`), any string a driver chooses for its own."""
    severity: Severity
    message: str
    since_ns: int
    """When it was raised; a repeated `set` keeps it."""
    scope: str
    """The owner's kind: a [Scope][flyball.foundation.device.state.Scope] -- `device`,
    `signal`, `controller`, `rig`."""
    subject: str
    """The owner's name: a device's or controller's name, a signal's address, the rig's."""
    details: Any = None


@dataclass(frozen=True, slots=True)
class Event:
    """Something that happened, for a log: a step failed, a pump clamped a request, a reader died.

    A [Condition][flyball.foundation.device.state.Condition] is what is true now and lives
    in state; an event is a point in time and lives in a stream and the
    session store. A condition's start and end are events too, told apart by
    `edge`.
    """

    time_ns: int
    severity: Severity
    scope: str
    """Which part: a [Scope][flyball.foundation.device.state.Scope] -- `device`, `signal`,
    `controller`, `program`, `rig`."""
    subject: str
    """The device, signal, controller, program step or rig it concerns."""
    code: str
    """Stable and machine-readable: a [Code][flyball.foundation.device.state.Code] --
    `step_failed`, `offline`. A plain string once read back from the store."""
    message: str
    details: Any = None
    edge: Edge | None = None
    """`raised` or `cleared` for a condition's edge; None for a point event."""
