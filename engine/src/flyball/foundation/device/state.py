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


class SubjectKind(StrEnum):
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
    """A condition: `reads.fail_after` reads in a row raised. Polling retries with backoff, and
    the first read that succeeds clears it."""
    GAVE_UP = "gave_up"
    """An event: an offline device's retries ran past `reads.give_up_after_s`, and its polling
    stopped. `offline` stays until a restart's first good read."""
    HUNG = "hung"
    """A condition: a poll's read has been in flight past `max(3·period, 5 s)` (stuck in its
    driver). Its read path is `stale(device_hung)` while it holds; cleared when the read
    returns. `error`; `details`: `{reading_s, bound_s}`."""
    SLOW = "slow"
    """A condition: its reads take longer than its period (de-flapped: see polling)."""
    DELIVERY_FAILED = "delivery_failed"
    WRITE_FAILED = "write_failed"
    """A condition: a write of the device failed -- its `commit`, on the delivery path or a
    blocking device's writer thread; cleared by the next that succeeds. The values it carried
    stay staged and are retried on the rig clock (A6). `commit_failed` before wave 2 stage 5."""
    RESENT = "resent"
    """An event: a commit set a value a failed write had kept: `re-sent <addr>=<v>, staged at
    T`. `details`: `{signal, value, staged_ns}`."""
    WRITE_DROPPED = "write_dropped"
    """An event: a value a failed write kept waited past the device's `retry_max_age_s` and was
    dropped, not sent; the demand stays `stale(write_failed)` until a new one commits.
    `details`: `{signal, value, age_s}`."""
    DEMAND_IGNORED = "demand_ignored"
    # A controller.
    STEP_FAILED = "step_failed"
    """A condition on a controller whose law raised; cleared when it steps again. A point event
    of a program's step, too."""
    STALE_INPUT = "stale_input"
    """A condition: the controller's measured signal is older than `stale_after_s`; held."""
    LIMIT_UNKNOWN = "limit_unknown"
    """A condition: a limit on the controller's output is not known; held. `info` while what
    it follows is only `pending` (an input not read yet), `warning` when it is `stale` or
    `invalid`. `details`: `{signal, unknown, why: {name: [quality, reason]}}`."""
    FROZEN = "frozen"
    """A condition on a regulating controller whose measured signal has no value: the law is
    not stepped and nothing is written. `info` for a benign no-value (`not_applicable`),
    `warning` for a fault (`invalid`, `stale`); `details`: `{signal, quality, reason}`. Cleared
    after `RESUME_AFTER` readings in a row with a value, when it regulates again."""
    # A signal.
    BAND_WARNING = "band_warning"
    """A condition on a signal (`warning`): its reading is outside its `warning` band but not
    its `alarm` band. Raised on the first reading beyond; cleared once readings have been back
    inside for `max(2·poll_s, 1 s)`. `details`: `{side, value, bounds}`."""
    BAND_ALARM = "band_alarm"
    """A condition on a signal (`error`): its reading is outside its `alarm` band. A signal
    holds this or `band_warning`, never both; same edges and `details`."""
    BAND_UNKNOWN = "band_unknown"
    """A condition on a banded signal whose `on_no_value` is `fire`: it has had no value
    because of a fault (`invalid`) for its grace, `max(2·poll_s, 1 s)`. An indication, counted
    in health `alarms.unknown` and never in `alarm`; `error` with an `alarm` band, else
    `warning`. Cleared after 3 readings in a row with a value. `details`: `{quality, reason,
    side}`."""
    INTERRUPTED = "interrupted"
    NOT_PERMITTED = "not_permitted"
    """A condition on a regulating controller: its output's `permissive` does not hold (or its
    signal has no value); held, as for `limit_unknown`. `details`: `{signal, permissive,
    value}`."""
    ON_FAULT = "on_fault"
    """An event on a controller: its source's outage was released and its `on_fault` action
    ran (`manual`, `stop`, `stop_device`; a law error takes at least `manual`). `details`:
    `{action, reason, accrued_s, was, stop}`."""
    RESEEDED = "reseeded"
    """An event on a controller: resuming after a hold, the trajectory it follows walks on
    from the reading at its own rate. `details`: `{end_was_s, end_s}` (rig time)."""
    # Stops and latches.
    STOPPED = "stopped"
    """A condition on the rig while its stop's latch holds: raised by the software stop,
    cleared by its Reset. `details`: `{by, at_ns, reason}`."""
    LATCHED = "latched"
    """A condition on each controller, signal and device a fault action latched; cleared by
    its Reset. `details`: `{cause, action, by, at_ns, reason}`."""
    STOP_APPLIED = "stop_applied"
    """An event on the rig: what a stop, a shutdown or a re-applied latch did, device by device,
    and every output it left energised (kept) with its value. `details`: `{why, devices,
    kept}`."""
    RESET = "reset"
    """An event: a person reset a latch (`details.cause`); nothing resumes."""
    WRITTEN_WHILE_STOPPED = "written_while_stopped"
    """An event on a signal: a person wrote it under the rig stop's latch, which holds on.
    `details`: `{value, by}`."""
    # A value an operator entered (`driver: values`).
    VALUE_WRITTEN = "value_written"
    """An event on the signal: an operator wrote a value. `details`: `{value, was, writer}`."""
    VALUE_RESTORED = "value_restored"
    """An event on the signal, at start: the value last written was restored from the store,
    the rig file's `initial` being unchanged. `details`: `{value, writer, written_ns}`."""
    VALUE_NOT_RESTORED = "value_not_restored"
    """A condition on the signal, at start: the value last written was not restored because
    its unit changed in the rig file; the file's `initial` is in force. Cleared by the next
    write. `details`: `{value, unit, now}`."""
    # A program (`step_failed` too). It ends `succeeded`, `failed`, `cancelled` by a person,
    # or `interrupted` by the engine (a stop, a shutdown), with the reason.
    STARTED = "started"
    STEP = "step"
    STEP_TIMED_OUT = "step_timed_out"
    STEP_STILL_RUNNING = "step_still_running"
    """The program was ended, but its step had not returned `END_JOIN_S` later (a command
    still in its driver): it may still act. The program is reported ended regardless."""
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RUN_FROM_LIBRARY = "run_from_library"
    # The rig.
    RECORDING_FAILED = "recording_failed"
    """A condition on the rig: the recorder stopped on a store error; cleared by the next
    recording that starts."""
    NOT_REVIVED = "not_revived"
    """A command on a device succeeded, but its polling was not restarted: a read of it has
    been in flight for longer than its period (hung in its driver). Not waited on."""
    RESTORED = "restored"
    EDIT_NOT_BUILT = "edit_not_built"
    """A condition on the rig (`error`), from the start after a rig edit whose rig did not
    build: the runner put back the version before it and started again on that. `details`:
    `{version, previous, error}`. Held until the next restart."""
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
    subject_kind: str
    """The owner's kind: a [SubjectKind][flyball.foundation.device.state.SubjectKind] -- `device`,
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
    subject_kind: str
    """Which part: a [SubjectKind][flyball.foundation.device.state.SubjectKind] -- `device`,
    `signal`, `controller`, `program`, `rig`."""
    subject: str
    """The device, signal, controller, program step or rig it concerns."""
    code: str
    """Stable and machine-readable: a [Code][flyball.foundation.device.state.Code] --
    `step_failed`, `offline`. A plain string once read back from the store."""
    message: str
    details: Any = None
    edge: Edge | None = None
    """`raised` or `cleared` for a condition's edge; None for a point event."""
