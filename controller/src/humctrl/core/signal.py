"""The wait primitive.

One concept: something fires, and says whether it was cancelled. Infrastructure
rather than an extension point -- user logic belongs in a
:class:`~humctrl.conditions.Condition`, which owns a signal.
"""

from threading import Event, Lock, Thread

from humctrl.core.utils import Labelled


class Outcome(Labelled):
    PENDING = "pending", "Still waiting"
    FIRED = "fired", "The condition was met"
    TIMEOUT = "timeout", "The time limit elapsed"
    INTERRUPTED = "interrupted", "Cancelled from outside"

    @property
    def settled(self) -> bool:
        return self is not Outcome.PENDING


class Signal(Event):
    outcome: Outcome = Outcome.PENDING

    def __init__(self, timeout: float | None = None) -> None:
        super().__init__()
        self._lock = Lock()
        self._timer: Thread | None = None
        if timeout is not None:
            self.set_timeout(timeout)

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
        Event.set(self)
        return True

    def fire(self) -> bool:
        return self._settle(Outcome.FIRED)

    def interrupt(self) -> bool:
        return self._settle(Outcome.INTERRUPTED)

    def expire(self) -> bool:
        return self._settle(Outcome.TIMEOUT)

    def set(self) -> None:
        self.interrupt()

    def clear(self) -> None:
        raise RuntimeError("a Signal is single-use; construct a new one")

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
