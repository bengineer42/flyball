"""Hardware writes off the delivery path: one thread per blocking device.

A delivery runs under the rig lock and must not wait for a bus. For a
device whose class says `blocking = True`, the rig routes what it would
have applied and committed through a [Writer][flyball.runtime.writer.Writer]:
the values are queued (the newest per signal wins), the writer's thread
applies them and runs the device's `commit`, and the write states it
reports go back to the rig -- published, delivered to the controllers,
recorded -- when the write completes. A bus that fails raises an event and
a condition the writer holds until a write succeeds; deliveries go on.
"""

from __future__ import annotations

import logging
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING

from flyball.core.device import Condition, Device, Level
from flyball.core.signal import Signal

if TYPE_CHECKING:
    from flyball.runtime.rig import Rig

log = logging.getLogger("flyball.writer")


class Writer:
    """Applies and commits to one blocking device on its own thread; the newest value wins."""

    def __init__(self, rig: Rig, device: Device) -> None:
        self.rig = rig
        self.device = device
        self.failed: Condition | None = None
        """Set while the last commit raised; cleared by the next that succeeds."""
        self.writes = 0
        self._queued: dict[Signal, tuple[int, float]] = {}
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
                for signal, (applied_ns, value) in queued.items():
                    self.device.apply(signal, applied_ns, value)
                states = self.device.commit(time_ns)
            except Exception as error:
                self._failure(error)
                continue
            self.writes += 1
            if self.failed is not None:
                self.failed = None
                self.rig.event(
                    Level.INFO, "device", self.device.name, "write_recovered", "writes succeed"
                )
            self.rig.written(self.device, states, time_ns)

    def _failure(self, error: Exception) -> None:
        message = f"{type(error).__name__}: {error}"
        first = self.failed is None
        self.failed = Condition("write_failed", Level.ERROR, message, self.rig.clock.now_ns())
        if first:  # one event per outage, not one per tick
            log.warning("%s: write failed: %s", self.device.name, message)
            self.rig.event(Level.ERROR, "device", self.device.name, "write_failed", message)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=1.0)


__all__ = ["Writer"]
