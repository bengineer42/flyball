"""The wait primitive.

One concept: something fires, and says whether it was cancelled. Infrastructure
rather than an extension point -- user logic belongs in an
:class:`~flyball.programmer.Activity`, which is a signal that knows how to hook
itself into a rig.
"""

from __future__ import annotations

from collections.abc import Callable
from threading import Event, Lock, Thread

from .utils import Labelled


class Outcome(Labelled):
    PENDING = "pending", "Still waiting"
    FIRED = "fired", "The condition was met"
    TIMEOUT = "timeout", "The time limit elapsed"
    INTERRUPTED = "interrupted", "Cancelled from outside"

    @property
    def settled(self) -> bool:
        return self is not Outcome.PENDING


class Signal:
    """Fires once, and says how it ended.

    Wraps an ``Event`` rather than being one: an Event's ``set``/``clear`` do
    not mean anything here, and hiding them keeps the outcome the only way to
    settle it.
    """

    __slots__ = ("_event", "_lock", "_timer", "on_settle", "outcome")

    outcome: Outcome
    #: Called once, with the signal, right after it settles -- from whichever
    #: thread settled it. For a registry that wants to publish the outcome.
    on_settle: Callable[[Signal], None] | None

    def __init__(self, timeout: float | None = None) -> None:
        self._event = Event()
        self._lock = Lock()
        self._timer: Thread | None = None
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
        """Record ``outcome`` and release waiters. False if already settled."""
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
        """Block until settled. False only if ``timeout`` elapsed first."""
        return self._event.wait(timeout)

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
