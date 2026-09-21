"""The wait primitive: something fires, and says whether it was cancelled.

Not an extension point; user logic belongs in an
[Activity][flyball.programmer.Activity].
"""

from __future__ import annotations

from collections.abc import Callable
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING

from ..primitives import Labelled

if TYPE_CHECKING:
    from ..time.clock import Clock


class Outcome(Labelled):
    PENDING = "pending", "Still waiting"
    FIRED = "fired", "The condition was met"
    TIMEOUT = "timeout", "The time limit elapsed"
    INTERRUPTED = "interrupted", "Cancelled from outside"

    @property
    def settled(self) -> bool:
        return self is not Outcome.PENDING


class Trigger:
    """Fires once, and says how it ended. Wraps an `Event`; settled only by an outcome."""

    __slots__ = ("_clock", "_event", "_lock", "_timer", "on_settle", "outcome")

    outcome: Outcome
    on_settle: Callable[[Trigger], None] | None
    """Called once with the signal after it settles, from the settling thread."""

    def __init__(self, timeout: float | None = None, clock: Clock | None = None) -> None:
        self._event = Event()
        self._lock = Lock()
        self._timer: Thread | None = None
        self._clock = clock  # the timeout counts in this clock's time; None is wall time
        self.on_settle = None
        self.outcome = Outcome.PENDING
        if timeout is not None:
            self.set_timeout(timeout)

    @property
    def settled(self) -> bool:
        return self.outcome.settled

    @property
    def interrupted(self) -> bool:
        return self.outcome is Outcome.INTERRUPTED

    @property
    def timed_out(self) -> bool:
        return self.outcome is Outcome.TIMEOUT

    @property
    def fired(self) -> bool:
        return self.outcome is Outcome.FIRED

    def _settle(self, outcome: Outcome) -> bool:
        """Record `outcome` and release waiters. False if already settled."""
        with self._lock:
            if self.outcome is not Outcome.PENDING:
                return False
            self.outcome = outcome
        self._event.set()
        if self.on_settle is not None:
            self.on_settle(self)
        return True

    def fire(self) -> bool:
        return self._settle(Outcome.FIRED)

    def interrupt(self) -> bool:
        return self._settle(Outcome.INTERRUPTED)

    def expire(self) -> bool:
        return self._settle(Outcome.TIMEOUT)

    def wait(self, timeout: float | None = None) -> bool:
        """Block until settled. False only if `timeout` elapsed first (in the clock's time)."""
        if self._clock is None:
            return self._event.wait(timeout)
        return self._clock.wait(self._event, timeout)

    def wait_outcome(self, timeout: float | None = None) -> Outcome:
        """Block until settled, then say how it ended."""
        self.wait(timeout)
        return self.outcome

    def set_timeout(self, timeout: float) -> None:
        if self._timer is not None:
            raise RuntimeError("timeout already set")
        self._timer = Thread(target=self._expire, args=(timeout,), daemon=True)
        self._timer.start()

    def _expire(self, duration: float) -> None:
        # wait() returns False only if the duration actually elapsed, so a
        # signal settled by someone else leaves the outcome alone.
        if not self.wait(duration):
            self.expire()
