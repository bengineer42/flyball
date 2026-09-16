"""Hardware writes off the delivery path: one thread per blocking actuator.

A loop ticks under the rig lock and must not wait for a bus. For an actuator
that declares `blocking = True`, the rig routes the loop's demands through a
[Writer][flyball.runtime.writer.Writer]: the newest demand is kept, the
writer's thread puts it on the wire, and what the actuator reports back
(`expected`) is what the loop sees on its next tick. A bus that fails raises
an event and a condition the rig reports; the loop goes on ticking.
"""

from __future__ import annotations

import logging
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING

from flyball.core.device import Condition, Level
from flyball.core.sink import Actuator

if TYPE_CHECKING:
    from flyball.runtime.rig import Rig

log = logging.getLogger("flyball.writer")


def is_blocking(actuator: Actuator) -> bool:
    """Whether `set_demand` may wait on a device: the actuator's class says `blocking = True`."""
    return bool(getattr(actuator, "blocking", False))


class Writer:
    """Carries demands to one actuator on its own thread; newest demand wins."""

    def __init__(self, rig: Rig, actuator: Actuator) -> None:
        self.rig = rig
        self.actuator = actuator
        self.expected: float | None = None
        """What the last completed write reported the actuator could deliver."""
        self.failed: Condition | None = None
        """Set while the last write raised; cleared by the next that succeeds."""
        self.writes = 0
        self._pending: float | None = None
        self._lock = Lock()
        self._wake = Event()
        self._stop = Event()
        self._thread = Thread(target=self._run, daemon=True, name=f"writer:{actuator.name}")
        self._thread.start()

    def request(self, demand: float) -> float | None:
        """Queue `demand` for the wire; return the last completed write's `expected`.

        Called by the loop under the rig lock: a store and a wake, no I/O.
        """
        with self._lock:
            self._pending = demand
        self._wake.set()
        return self.expected

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            with self._lock:
                demand, self._pending = self._pending, None
                self._wake.clear()
            if demand is None:
                continue
            try:
                expected = self.actuator.set_demand(demand)
            except Exception as error:
                self._failure(demand, error)
                continue
            self.expected = expected
            self.writes += 1
            if self.failed is not None:
                self.failed = None
                self.rig.event(
                    Level.INFO, "actuator", self.actuator.name, "write_recovered", "writes succeed"
                )
            self.rig.apply(self.actuator)  # publish the state the write produced

    def _failure(self, demand: float, error: Exception) -> None:
        message = f"{type(error).__name__}: {error}"
        first = self.failed is None
        self.failed = Condition("write_failed", Level.ERROR, message, self.rig.clock.now_ns())
        if first:  # one event per outage, not one per tick
            log.warning("%s: write of %g failed: %s", self.actuator.name, demand, message)
            self.rig.event(
                Level.ERROR,
                "actuator",
                self.actuator.name,
                "write_failed",
                message,
                {"demand": demand},
            )

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=1.0)


__all__ = ["Writer", "is_blocking"]
