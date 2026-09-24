"""Hardware writes off the delivery path: one thread per blocking device.

A delivery runs under the rig lock and must not wait for a bus. For a
device whose class says `blocking = True`, the rig routes what it would
have applied and committed through a [Writer][flyball.runtime.writer.Writer]:
the values are queued (the newest per signal wins), the writer's thread
applies them and runs the device's `commit`, and the write states it
reports go back to the rig -- published, delivered to the controllers,
recorded -- when the write completes. A bus that fails raises a
`write_failed` condition on the device, cleared by the next write that
succeeds; deliveries go on.

A write that fails keeps its values (A6): they go back in the queue, unless a
newer value on the same signal arrived meanwhile, and go out with the next
write. The rig arms a retry on its clock, which calls
[retry][flyball.runtime.writer.Writer.retry] after dropping what has waited
past `retry_max_age_s` ([drop_older_than][flyball.runtime.writer.Writer.drop_older_than]).
"""

from __future__ import annotations

import logging
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING

from flyball.foundation.device import Code, Committable, Condition, Severity, Signal

if TYPE_CHECKING:
    from flyball.rig import Rig

log = logging.getLogger("flyball.writer")


class Writer:
    """Applies and commits to one blocking device on its own thread; the newest value wins."""

    def __init__(self, rig: Rig, device: Committable) -> None:
        self.rig = rig
        self.device = device
        self.writes = 0
        self._queued: dict[Signal, tuple[int, float]] = {}
        self._restaged: set[Signal] = set()
        """Signals whose queued value a failed write kept: sending one is a re-send."""
        self._commit_ns: int | None = None
        self._lock = Lock()
        self._wake = Event()
        self._stop = Event()
        self._thread = Thread(target=self._run, daemon=True, name=f"writer:{device.name}")
        self._thread.start()

    def apply(self, signal: Signal, time_ns: int, value: float) -> None:
        """Queue one value for the device's `apply`, replacing any earlier one on `signal`."""
        with self._lock:
            self._queued[signal] = (time_ns, value)
            self._restaged.discard(signal)  # a newer value: not a re-send

    def request(self, time_ns: int) -> None:
        """Ask for a commit at `time_ns`; a store and a wake, no I/O."""
        with self._lock:
            self._commit_ns = time_ns
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            with self._lock:
                queued, self._queued = self._queued, {}
                time_ns, self._commit_ns = self._commit_ns, None
                self._wake.clear()
            if time_ns is None:
                continue
            try:
                self._write(queued, time_ns)
            except Exception:  # the thread outlives anything, or the device is never written again
                log.exception("%s: writer", self.device.name)

    def retry(self, time_ns: int) -> bool:
        """Commit what failed writes kept, now; False when nothing is kept."""
        with self._lock:
            if not self._queued:
                return False
        self.request(time_ns)
        return True

    def drop_older_than(self, cutoff_ns: int) -> list[tuple[Signal, float, int]]:
        """Take out every queued value applied before `cutoff_ns`: `(signal, value, applied_ns)`."""
        with self._lock:
            old = [(s, v, t) for s, (t, v) in self._queued.items() if t < cutoff_ns]
            for signal, _, _ in old:
                del self._queued[signal]
                self._restaged.discard(signal)
        return old

    def _write(self, queued: dict[Signal, tuple[int, float]], time_ns: int) -> None:
        """One write: apply, commit, then report it to the rig.

        A commit that raises keeps what it was given, for the next write, and
        is a failure. So is a report that raises -- the write reached the
        device, but the rig does not know what it set -- so its values are kept
        and sent again, and it is logged rather than ending the thread.
        """
        try:
            for signal, (applied_ns, value) in queued.items():
                self.device.apply(signal, applied_ns, value)
            # What the router has counted per signal until now: a signal
            # pushed from here on is the commit's readback.
            before = dict(self.rig.router.seq)
            self.device.commit(time_ns)
        except Exception as error:
            self.device.staged.clear()
            self._failure(error, queued)
            return
        self.writes += 1
        with self._lock:
            resent = [(s, v, t) for s, (t, v) in queued.items() if s in self._restaged]
            self._restaged.difference_update(queued)
        try:
            self.rig.written(self.device, time_ns, before)
        except Exception as error:
            log.exception("%s: reporting a write", self.device.name)
            self._failure(error, queued)
            return
        if self.rig.conditions.clear(self.device, Code.WRITE_FAILED, message="writes succeed"):
            self.rig.writes_recovered(self.device)  # its echo demands are known again
        if resent:
            self.rig.resent(self.device, resent)

    @property
    def failed(self) -> Condition | None:
        """The device's `write_failed` while the last write failed; None once one succeeds."""
        return self.rig.conditions.get(self.device, Code.WRITE_FAILED)

    def _failure(self, error: Exception, queued: dict[Signal, tuple[int, float]]) -> None:
        """Keep the values, and hold `write_failed`.

        The device's echo demands read `stale(write_failed)` meanwhile, and the rig
        arms a retry.
        """
        with self._lock:
            for signal, entry in queued.items():
                if signal not in self._queued:  # a newer value on it wins
                    self._queued[signal] = entry
                    self._restaged.add(signal)
        message = f"{type(error).__name__}: {error}"
        # Raised once per outage, not once per tick: a held condition is only updated.
        if self.rig.conditions.set(self.device, Code.WRITE_FAILED, Severity.ERROR, message):
            log.warning("%s: write failed: %s", self.device.name, message)
        self.rig.writes_failed(self.device, queued)

    def stop(self, join: bool = True) -> None:
        """Stop the thread after the write in progress, if any.

        `join=False` under the rig lock: a write completing reports through
        [written][flyball.rig.rig.Rig.written], which takes that lock.
        """
        self._stop.set()
        self._wake.set()
        if join:
            self._thread.join(timeout=1.0)


__all__ = ["Writer"]
