"""What is true now, keyed by who it is true of: the condition store and its edges.

A condition is held under `(owner, code)`, where the owner is the object it
is true of -- a device, a signal, a controller, the rig -- not its name, so a
device removed and another added under the same name start clean. `set`
raises a condition and `clear` ends it; each is an event only on a genuine
transition (absent to present: `raised`; present to absent: `cleared`, with
how long it held). Setting a held condition again updates its message and
nothing else: a producer may call `set` every poll or every step without
flooding the log. That is the de-duplication rule; a producer that must
not flicker (a read that is sometimes slow) adds its own hysteresis before
it calls either.

Edges also go to in-process subscribers (rules, alerts), on a thread of the
store's own, in the order they happened: a subscriber never runs on the
producer's thread, so never under the rig's lock, and may do I/O.

This module knows no rig. A device keeps a store of its own until a rig
adds it (a driver may raise a condition in its constructor); the rig then
takes what it holds into the rig's store, which records the edges as
events.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from queue import Queue
from threading import Lock, Thread
from typing import Any

from .state import Condition, Edge, Event, Severity, SubjectKind

log = logging.getLogger("flyball.conditions")


@dataclass(frozen=True, slots=True)
class ConditionEdge:
    """One transition, as a subscriber hears it: whose, which condition, and the event."""

    owner: object
    """The object the condition is true of: a `Device`, a `Signal`, a `Controller`, the rig."""
    condition: Condition
    """The condition as it was raised, or as it stood when it cleared."""
    event: Event
    """The event recorded for it: `edge`, `code`, `severity`, `subject_kind`, `subject`,
    `time_ns`."""


type Describe = Callable[[object], tuple[str, str]]
"""An owner's `(subject_kind, subject)`: `("device", "furnace")`."""


def describe(owner: object) -> tuple[str, str]:
    """A device's or a signal's kind and name, without importing either (no cycle)."""
    from .device import Device
    from .signal import Signal

    if isinstance(owner, Device):
        return SubjectKind.DEVICE, owner.name
    if isinstance(owner, Signal):
        return SubjectKind.SIGNAL, owner.address
    raise TypeError(f"{type(owner).__name__} cannot own a condition here")


class Conditions:
    """Every condition held now, by owner object and code, with an edge on each transition.

    Thread-safe: producers run on polling threads, writer threads and under
    the rig's lock alike. `emit` is called with each edge's event, under the
    store's own lock so edges are recorded in the order they happened; it
    must not take the rig's lock (the rig's records and publishes, which do
    not).
    """

    def __init__(
        self,
        *,
        now_ns: Callable[[], int] = time.time_ns,
        describe: Describe = describe,
        emit: Callable[[Event], None] | None = None,
    ) -> None:
        self.now_ns = now_ns
        self._describe = describe
        self._emit = emit
        self._held: dict[tuple[int, str], tuple[object, Condition]] = {}
        """By `(id(owner), code)`: identity, not equality (two signals may compare equal); the
        owner is kept beside its condition, so its id is not reused while it is held."""
        self._lock = Lock()
        self._subscribers: list[Callable[[ConditionEdge], None]] = []
        self._queue: Queue[ConditionEdge | None] | None = None
        self._thread: Thread | None = None

    # region Transitions

    def set(
        self,
        owner: object,
        code: str,
        severity: Severity,
        message: str,
        details: Any = None,
    ) -> bool:
        """Hold `code` on `owner`; whether that raised it (it was not held before).

        Held already: its message, severity and details are replaced and
        `since_ns` kept, with no event.
        """
        with self._lock:
            key = (id(owner), str(code))
            if (entry := self._held.get(key)) is not None:
                held = replace(entry[1], severity=severity, message=message, details=details)
                self._held[key] = (owner, held)
                return False
            subject_kind, subject = self._describe(owner)
            now = self.now_ns()
            condition = Condition(code, severity, message, now, subject_kind, subject, details)
            self._held[key] = (owner, condition)
            event = Event(now, severity, subject_kind, subject, code, message, details, Edge.RAISED)
            self._transition(owner, condition, event)
            return True

    def clear(
        self,
        owner: object,
        code: str,
        details: Any = None,
        *,
        message: str | None = None,
    ) -> Condition | None:
        """End `code` on `owner`: the condition that was held, or None (and no event) if none was.

        The `cleared` event is `info`, says how long it held (`details.duration_s`,
        beside any `details` given) and carries `message`, or "cleared" and
        what it was.
        """
        with self._lock:
            entry = self._held.pop((id(owner), str(code)), None)
            if entry is None:
                return None
            condition = entry[1]
            now = self.now_ns()
            duration_s = (now - condition.since_ns) / 1e9
            event = Event(
                now,
                Severity.INFO,
                condition.subject_kind,
                condition.subject,
                condition.code,
                message or f"cleared: {condition.message}",
                {**(details or {}), "duration_s": duration_s},
                Edge.CLEARED,
            )
            self._transition(owner, condition, event)
            return condition

    def note(
        self,
        owner: object,
        code: str,
        severity: Severity,
        message: str,
        details: Any = None,
    ) -> Event:
        """A point event about `owner`: recorded, nothing held, no edge, no subscriber hears it.

        What happened once rather than what is true now: a blend flow resolved,
        a request clamped.
        """
        subject_kind, subject = self._describe(owner)
        event = Event(self.now_ns(), severity, subject_kind, subject, str(code), message, details)
        if self._emit is not None:
            try:
                self._emit(event)
            except Exception:  # a point event is never lost to a failing log or recorder
                log.exception("recording %s", event.code)
        return event

    def clear_owner(self, owner: object, *, reason: str = "removed") -> list[Condition]:
        """End every condition `owner` holds, one `cleared` edge each: removed, or detached."""
        with self._lock:
            codes = [code for (key, code) in self._held if key == id(owner)]
        return [
            cleared
            for code in codes
            if (cleared := self.clear(owner, code, {"reason": reason}, message=reason)) is not None
        ]

    def _transition(self, owner: object, condition: Condition, event: Event) -> None:
        """Under the lock: record the edge, then queue it for the subscribers."""
        if self._emit is not None:
            try:
                self._emit(event)
            except Exception:  # an edge is never lost to a failing log or recorder
                log.exception("recording %s %s", event.edge, event.code)
        if self._queue is not None:
            self._queue.put(ConditionEdge(owner, condition, event))

    # endregion

    # region Reading

    def get(self, owner: object, code: str) -> Condition | None:
        """`code` on `owner`, if held."""
        with self._lock:
            entry = self._held.get((id(owner), str(code)))
            return None if entry is None else entry[1]

    def of(self, owner: object) -> list[Condition]:
        """What `owner` holds now, in the order raised."""
        with self._lock:
            return [c for held, c in self._held.values() if held is owner]

    def all(self) -> list[Condition]:
        """Every condition held now, in the order raised. A snapshot: no lock is kept."""
        with self._lock:
            return [c for _, c in self._held.values()]

    def items(self) -> list[tuple[object, Condition]]:
        """Every condition held now with its owner, in the order raised."""
        with self._lock:
            return list(self._held.values())

    # endregion

    # region Subscribers

    def subscribe(self, callback: Callable[[ConditionEdge], None]) -> Callable[[], None]:
        """Call `callback` with every edge from now on; returns the call that unsubscribes.

        Called on the store's own thread, in order, never on the producer's
        (so never under the rig's lock). One that raises is logged and the
        rest still hear the edge.
        """
        with self._lock:
            self._subscribers = [*self._subscribers, callback]
            if self._queue is None:
                self._queue = Queue()
                self._thread = Thread(target=self._dispatch, daemon=True, name="conditions")
                self._thread.start()

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers = [s for s in self._subscribers if s is not callback]

        return unsubscribe

    def flush(self, timeout: float | None = None) -> bool:
        """Wait until every edge so far has reached the subscribers; whether it did in time."""
        queue = self._queue
        if queue is None:
            return True
        deadline = None if timeout is None else time.monotonic() + timeout
        while queue.unfinished_tasks:
            if deadline is not None and time.monotonic() > deadline:
                return False
            time.sleep(0.005)
        return True

    def close(self) -> None:
        """Stop the subscribers' thread after the edges already queued."""
        with self._lock:
            queue, self._queue = self._queue, None
        if queue is not None:
            queue.put(None)

    def _dispatch(self) -> None:
        queue = self._queue
        assert queue is not None
        while (edge := queue.get()) is not None:
            for callback in self._subscribers:
                try:
                    callback(edge)
                except Exception:
                    log.exception("condition subscriber %r failed", callback)
            queue.task_done()
        queue.task_done()

    # endregion


__all__ = ["ConditionEdge", "Conditions", "describe"]
