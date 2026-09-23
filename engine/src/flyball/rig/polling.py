"""Runs each device's `read` on its period and keeps what the runtime knows of the run.

A device is polled at the smallest `poll_s` in its tree: the device's own,
inherited down, or any namespace's or signal's own. Which signals are
due at a given call is the driver's business inside `read`; the runtime
only knocks often enough. What the runtime knows -- period, last delivery,
whether the device is stopped on an error -- lives here, beside the
device's own state, and is pushed through `runs`. What goes wrong with the
reads -- `offline`, `slow` -- is a condition on the device, in the rig's
condition store.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from flyball.foundation.device import Code, Device, Readable, Sample, Scope, Severity
from flyball.foundation.errors import NotFoundError
from flyball.foundation.router import Latest
from flyball.foundation.time import PeriodicLoop

log = logging.getLogger("flyball.polling")

STOP_JOIN_S = 2.0
"""How long `stop_all` waits, over all polling threads together, for reads in progress."""

SLOW_AFTER = 3
"""Reads in a row over the period before a device is `slow`."""
FAST_AFTER = 5
"""Reads in a row at or under `FAST_FRACTION` of the period before `slow` clears."""
FAST_FRACTION = 0.8

if TYPE_CHECKING:
    from .rig import Rig


def poll_period(device: Device) -> float | None:
    """The period the runtime polls `device` on: the smallest over its published signals.

    Each signal's `poll_s` is already the nearest one up the tree, so the
    device's own and any namespace's own are counted through it. None
    when nothing publishes on a period: the device is never polled.
    """
    periods = [s.poll_s for s in device.published.values() if s.poll_s is not None]
    return min(periods) if periods else None


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceRun:
    """How the runtime is polling a device, beside what the device reports of itself."""

    period_s: float | None = None
    running: bool = False
    last_read_ns: int | None = None
    read_s: float | None = None
    """How long the last read took, in the rig's time: `read` alone, not its delivery."""
    missed: int = 0
    """How many reads took longer than the period since polling began."""


class Polling:
    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self.by_name: dict[str, Device] = {}
        self.periodic: dict[str, PeriodicLoop] = {}
        self.runs: Latest[str, DeviceRun] = Latest()
        """The newest run of each polled device, by name, after every read or failure."""
        self._runs: dict[str, DeviceRun] = {}
        self._streaks: dict[str, tuple[int, int]] = {}
        """Per device: reads in a row over the period, and in a row well under it."""

    def get(self, name: str) -> Device:
        try:
            return self.by_name[name]
        except KeyError as e:
            raise NotFoundError(f"Polled device {name!r} not found") from e

    def run(self, name: str) -> DeviceRun:
        return self._runs[name]

    def start(self, device: Device, period_s: float) -> None:
        """Poll `device` every `period_s`; one already polled is restarted on the new period."""
        self.by_name[device.name] = device
        self._runs.setdefault(device.name, DeviceRun())
        self._streaks.pop(device.name, None)  # a new period: count reads against it afresh
        if (loop := self.periodic.pop(device.name, None)) is not None:
            loop.stop()
        loop = PeriodicLoop(self._read, period_s, False, device, clock=self.rig.clock)
        self.periodic[device.name] = loop
        self._update(device, period_s=period_s, running=True)
        loop.start()

    def restart(self, name: str) -> DeviceRun:
        """Poll an offline device again on its period.

        Raises:
            NotFoundError: No such polled device.
        """
        device = self.get(name)
        period = self._runs[name].period_s
        # Cleared before the loop starts: a device still broken fails its
        # first read on the loop's thread, and that `offline` must be raised
        # again after the clearing, not be wiped by it.
        self.rig.conditions.clear(device, Code.OFFLINE, message="polling again")
        if period is not None:
            self.start(device, period)
        return self._runs[name]

    def revive(self, name: str) -> bool:
        """Poll `name` again if it is polled on a period and has gone offline.

        Called after a command on the device succeeds -- from the HTTP route
        and from a program step alike -- since a command that runs on an
        offline device is taken as the fix (`restore`, a reset, a reconnect).
        A device still broken goes offline again with a fresh event.

        Returns:
            Whether polling was restarted.
        """
        run = self._runs.get(name)
        if name not in self.by_name or run is None or run.running or run.period_s is None:
            return False
        self.restart(name)
        return True

    def stop(self, name: str) -> None:
        """Stop polling one device and forget it; a device that was never polled is a no-op.

        Does not wait for a read in progress: the caller holds the rig lock,
        which that read's delivery needs. The read finishes on its own
        thread, finds the device no longer polled, and drops what it read.
        """
        if (loop := self.periodic.pop(name, None)) is not None:
            loop.stop(join=False)
        self.by_name.pop(name, None)
        self._runs.pop(name, None)
        self._streaks.pop(name, None)
        self.runs.discard(name)

    def stop_all(self) -> None:
        """Stop polling every device, waiting at most `STOP_JOIN_S` for reads in progress.

        A read stuck in its driver is not waited on for ever -- SIGTERM and a
        daemon restart both come through here. Its thread (a daemon thread)
        is abandoned and logged once by name; if the read ever returns, the
        loop exits without reading again.
        """
        for loop in self.periodic.values():
            loop.stop(join=False)
        deadline = time.monotonic() + STOP_JOIN_S
        for name, loop in self.periodic.items():
            loop.stop(timeout=max(0.0, deadline - time.monotonic()))
            if loop.running:
                log.warning("gave up waiting for %s's read after %.1f s", name, STOP_JOIN_S)
            self._update(self.by_name[name], running=False)

    def delivered(self, device: Device, samples: Sequence[Sample]) -> None:
        """Samples `device` read: into the rig, then noted as its latest.

        A failure *downstream* that the delivery does not keep to itself (a
        law's and a driver's `commit` are) is the rig's, not the read's: it
        becomes an event and the device carries on, its samples still noted
        as read.
        """
        try:
            with self.rig.lock:
                if not self._polled(device):
                    return  # removed while it was being read
                self.rig.on_samples(samples)
        except Exception as error:
            log.exception("delivering %s's samples", device.name)
            self.rig.event(
                Severity.ERROR,
                Scope.RIG,
                device.name,
                Code.DELIVERY_FAILED,
                f"{type(error).__name__}: {error}",
                {"device": device.name},
            )
        if (run := self._runs.get(device.name)) is None:
            return
        last_read_ns = max(s.time_ns for s in samples) if samples else run.last_read_ns
        self._update(device, last_read_ns=last_read_ns)

    def _read(self, device: Device) -> None:
        """One scheduled poll: deliver what it returns -- or note the failure and stop polling.

        Only `read` itself can put the device offline; delivery failures are
        handled in [delivered][flyball.rig.polling.Polling.delivered].
        A device that went offline stays stopped until `restart`. Only
        `read` is timed for `slow`: the delivery after it, and the rig lock
        it waits for, are not the device's.
        """
        try:
            if not isinstance(device, Readable):
                raise TypeError(f"{type(device).__name__} has nothing to read")
            with device.read_lock:  # not beside a fresh read of it; never the rig's lock
                started = self.rig.clock.monotonic()  # in the rig's time, as the period is
                samples = tuple(device.read(self.rig.clock.now_ns()))
                read_s = self.rig.clock.monotonic() - started
        except Exception as error:
            if not self._polled(device):
                return  # removed while it was being read: nothing to put offline
            self._update(device, running=False)
            self.rig.conditions.set(
                device, Code.OFFLINE, Severity.ERROR, f"{type(error).__name__}: {error}"
            )
            if (loop := self.periodic.get(device.name)) is not None:
                loop.stop(join=False)  # from inside the loop: it exits after this call
            return
        if not self._polled(device):
            return  # removed while it was being read: what it read goes nowhere
        self.delivered(device, samples)
        self._paced(device, read_s)

    def _paced(self, device: Device, read_s: float) -> None:
        """Note how long a read took; raise `slow` on a run of slow reads, clear it on fast ones.

        Raised after `SLOW_AFTER` reads in a row over the period; cleared
        after `FAST_AFTER` in a row at or under `FAST_FRACTION` of it. A read
        between the two starts both counts again. So a read that is slow now
        and then raises nothing, and one that hovers at its period does not
        flicker: one `raised` and one `cleared` per spell, whatever it does
        in between.
        """
        if (run := self._runs.get(device.name)) is None:
            return
        period = run.period_s
        over = period is not None and read_s > period
        self._update(device, read_s=read_s, missed=run.missed + over)
        if period is None:
            return
        slow, fast = self._streaks.get(device.name, (0, 0))
        if over:
            slow, fast = slow + 1, 0
        elif read_s <= FAST_FRACTION * period:
            slow, fast = 0, fast + 1
        else:
            slow, fast = 0, 0
        self._streaks[device.name] = (slow, fast)
        held = self.rig.conditions.get(device, Code.SLOW) is not None
        if over and (held or slow >= SLOW_AFTER):
            self.rig.conditions.set(
                device,
                Code.SLOW,
                Severity.WARNING,
                f"reads take longer than the {period:g} s period: the last took {read_s:.2f} s",
                {"read_s": read_s, "period_s": period},
            )
        elif held and fast >= FAST_AFTER:
            self.rig.conditions.clear(
                device, Code.SLOW, message=f"reads keep up again: the last took {read_s:.2f} s"
            )

    def touch(self, name: str) -> None:
        """Push `name`'s run to watchers again unchanged: its conditions changed, not its run."""
        if (run := self._runs.get(name)) is not None and self.runs.watched:
            self.runs.set(name, run)

    def _polled(self, device: Device) -> bool:
        """Whether `device` itself is still polled: not removed, nor replaced under its name."""
        return self.by_name.get(device.name) is device

    def _update(self, device: Device, **changes: Any) -> None:
        """Note a change to `device`'s run; a no-op once it is no longer polled."""
        if not self._polled(device) or (run := self._runs.get(device.name)) is None:
            return
        run = replace(run, **changes)
        self._runs[device.name] = run
        if self.runs.watched:
            self.runs.set(device.name, run)
