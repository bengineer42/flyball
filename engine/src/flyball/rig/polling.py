"""Runs each device's `read` on its period and keeps what the runtime knows of the run.

A device is polled at the smallest `poll_s` in its tree: the device's own,
inherited down, or any namespace's or signal's own. Which signals are
due at a given call is the driver's business inside `read`; the runtime
only knocks often enough. What the runtime knows -- period, last delivery,
a read in flight, failures in a row, the next retry -- lives here, beside
the device's own state, and is pushed through `runs`. What goes wrong with
the reads -- `offline`, `slow` -- is a condition on the device, in the rig's
condition store.

A read that raises counts toward the device's failure budget
(`ReadPolicy.fail_after`); below it the loop carries on at its period. At
the budget the device is `offline` and the loop keeps running: it reads
again after each wait of `backoff_s` in turn, the last repeating, until a
read succeeds, which clears `offline` and puts the loop back on its period.
The budget is per device, not per namespace: the runtime calls one `read`
per period, and a raise ends that call, whichever namespace it came from.

A read still in flight `max(3·period, 5 s)` after it began is stuck in its
driver: the device holds `hung` (a one-shot on the rig clock raises it), and
what its reads delivered is `stale(device_hung)` at once. The read returning,
however it returns, clears `hung`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from flyball.foundation.device import Code, Device, Readable, Sample, Severity, SubjectKind
from flyball.foundation.errors import ConflictError, NotFoundError
from flyball.foundation.router import Latest
from flyball.foundation.time import PeriodicLoop

from .liveness import hung_after_s

log = logging.getLogger("flyball.polling")

STOP_JOIN_S = 2.0
"""How long `stop_all` waits, over all polling threads together, for reads in progress."""

SLOW_AFTER = 3
"""Reads in a row over the period before a device is `slow`."""
FAST_AFTER = 5
"""Reads in a row at or under `FAST_FRACTION` of the period before `slow` clears."""
FAST_FRACTION = 0.8

FAIL_AFTER = 3
"""Reads that raise in a row before a device is `offline`, unless the rig file says."""
BACKOFF_S = (1.0, 2.0, 5.0, 15.0, 60.0)
"""The waits between an offline device's retries, in turn; the last repeats for ever."""

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


@dataclass(frozen=True, slots=True)
class ReadPolicy:
    """When failed reads put a device offline, and how it is retried: `reads:`, resolved."""

    fail_after: int = FAIL_AFTER
    backoff_s: tuple[float, ...] = BACKOFF_S
    give_up_after_s: float | None = None
    """Stop retrying this long after `offline` was raised; None: never."""

    def backoff(self, retry: int) -> float:
        """The wait before retry `retry` (0: the first after going offline)."""
        return self.backoff_s[min(retry, len(self.backoff_s) - 1)]


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
    reading_since_ns: int | None = None
    """When the read in flight began, on the rig's clock; None: none is."""
    consecutive_failures: int = 0
    """Reads in a row that raised; 0 after one that succeeds."""
    next_retry_ns: int | None = None
    """While offline and retrying: when the next read is due, on the rig's clock."""


class Polling:
    """Every polled device's loop and run.

    Its dicts are changed from the poll threads (a run noted, a device gone
    offline), from the rig's lock holders (a device added, removed) and from
    request threads (a restart), so each change and each read of more than one
    key is under `_lock`. That lock is never held while the rig's is taken, nor
    across a join: the order is rig, then polling.
    """

    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self._lock = threading.RLock()
        self.by_name: dict[str, Device] = {}
        self.periodic: dict[str, PeriodicLoop] = {}
        self.runs: Latest[str, DeviceRun] = Latest()
        """The newest run of each polled device, by name, after every read or failure."""
        self._runs: dict[str, DeviceRun] = {}
        self._streaks: dict[str, tuple[int, int]] = {}
        """Per device: reads in a row over the period, and in a row well under it."""
        self._reading: dict[str, float] = {}
        """Per device with a poll's `read` in flight: when it began, in the rig's time."""
        self.defaults = ReadPolicy()
        """The rig's `reads:` (the runner's); a device's own keys win."""
        self._policies: dict[str, ReadPolicy] = {}
        """Per polled device: its `reads:` resolved when its polling started."""

    def policy(self, device: Device) -> ReadPolicy:
        """`device`'s `reads:`: its rig-file entry's keys, then the rig's, then the defaults."""
        entry = self.rig.entries.get(device.name)
        own = None if entry is None else entry.reads
        if own is None:
            return self.defaults
        return ReadPolicy(
            self.defaults.fail_after if own.fail_after is None else own.fail_after,
            self.defaults.backoff_s if own.backoff_s is None else tuple(own.backoff_s),
            own.give_up_after_s,
        )

    def get(self, name: str) -> Device:
        try:
            return self.by_name[name]
        except KeyError as e:
            raise NotFoundError(f"Polled device {name!r} not found") from e

    def run(self, name: str) -> DeviceRun:
        return self._runs[name]

    def snapshot(self) -> dict[str, DeviceRun]:
        """Every polled device's run now, by name: one C-level copy, safe without any lock."""
        return dict(list(self._runs.items()))

    def reading_for(self, device: Device) -> float | None:
        """How long a read of `device` has been in flight, in the rig's time; None: none is.

        A poll's is timed; a fresh read's only shows as in flight (0).
        """
        with self._lock:
            began = self._reading.get(device.name)
        if began is not None:
            return max(0.0, self.rig.clock.monotonic() - began)
        return 0.0 if device.read_lock.locked() else None

    def start(self, device: Device, period_s: float) -> None:
        """Poll `device` every `period_s`; one already polled is restarted on the new period.

        Raises:
            ConflictError: The old loop was still in a read after `STOP_JOIN_S`: it is
                stopped (it exits when the read returns), and no new one is started.
        """
        with self._lock:
            old = self.periodic.pop(device.name, None)
        if old is not None:
            old.stop(timeout=STOP_JOIN_S)  # outside the lock: its read may note its run
            if old.running:
                self._update(device, running=False)
                raise ConflictError(
                    f"'{device.name}': a read did not return within {STOP_JOIN_S:g} s;"
                    " polling is stopped. Try again when it returns"
                )
        loop = PeriodicLoop(self._read, period_s, False, device, clock=self.rig.clock)
        with self._lock:
            self.by_name[device.name] = device
            self._runs.setdefault(device.name, DeviceRun())
            self._streaks.pop(device.name, None)  # a new period: count reads against it afresh
            self._policies[device.name] = self.policy(device)
            self.periodic[device.name] = loop
            self._update(device, period_s=period_s, running=True, next_retry_ns=None)
        with self.rig.lock:  # its signals are judged on its period from now (liveness)
            if self.rig.devices.get(device.name) is device:
                self.rig.liveness.watch(device, polled=True)
        loop.start()

    def restart(self, name: str) -> DeviceRun:
        """Poll a device again on its period: its next read one period from now.

        For a device that is offline, retrying or given up: it is read again
        without waiting out its backoff. It clears nothing: `offline` stays,
        and the failures in a row are kept, until a read succeeds. Refused
        while a read of the device is in flight, rather than wait on a device
        that may be hung in its driver.

        Raises:
            NotFoundError: No such polled device.
            ConflictError: A read of it is in flight.
        """
        device = self.get(name)
        if (in_flight := self.reading_for(device)) is not None:
            raise ConflictError(
                f"'{name}': a read has been in flight for {in_flight:.1f} s;"
                " restart it when that read returns"
            )
        period = self._runs[name].period_s
        if period is not None:
            self.start(device, period)
        return self._runs[name]

    def revive(self, name: str) -> bool:
        """Poll `name` again now if it is polled on a period and is offline or stopped.

        Called after a command on the device succeeds -- from the HTTP route
        and from a program step alike -- since a command that runs on an
        offline device is taken as the fix (`restore`, a reset, a reconnect):
        it is read one period later rather than at the end of its backoff. A
        device still broken stays offline and backs off again. One whose poll
        is stuck in a read for longer than its period (hung) is not revived,
        nor waited on: a `not_revived` event on the device says so.

        Returns:
            Whether polling was restarted.
        """
        with self._lock:
            run = self._runs.get(name)
            device = self.by_name.get(name)
        if device is None or run is None or run.period_s is None:
            return False
        offline = self.rig.conditions.get(device, Code.OFFLINE) is not None
        if run.running and not offline:
            in_flight = self.reading_for(device)
            if in_flight is not None and in_flight > run.period_s:
                self.rig.event(
                    Severity.WARNING,
                    SubjectKind.DEVICE,
                    name,
                    Code.NOT_REVIVED,
                    f"the command succeeded, but a read has been in flight for {in_flight:.1f} s"
                    f" (period {run.period_s:g} s): polling not restarted",
                    {"reading_s": in_flight, "period_s": run.period_s},
                )
            return False
        try:
            self.restart(name)
        except ConflictError as error:  # a read in flight after all: say so, don't wait
            self.rig.event(Severity.WARNING, SubjectKind.DEVICE, name, Code.NOT_REVIVED, str(error))
            return False
        return True

    def stop(self, name: str) -> None:
        """Stop polling one device and forget it; a device that was never polled is a no-op.

        Does not wait for a read in progress: the caller holds the rig lock,
        which that read's delivery needs. The read finishes on its own
        thread, finds the device no longer polled, and drops what it read.
        """
        with self._lock:
            loop = self.periodic.pop(name, None)
            self.by_name.pop(name, None)
            self._runs.pop(name, None)
            self._streaks.pop(name, None)
            self._policies.pop(name, None)
            self.runs.discard(name)
        if loop is not None:
            loop.stop(join=False)

    def stop_all(self) -> None:
        """Stop polling every device, waiting at most `STOP_JOIN_S` for reads in progress.

        A read stuck in its driver is not waited on for ever -- SIGTERM and a
        daemon restart both come through here. Its thread (a daemon thread)
        is abandoned and logged once by name; if the read ever returns, the
        loop exits without reading again.
        """
        with self._lock:  # a copy: a device may be added or removed meanwhile
            loops = [(name, loop, self.by_name[name]) for name, loop in self.periodic.items()]
        for _, loop, _ in loops:
            loop.stop(join=False)
        deadline = time.monotonic() + STOP_JOIN_S
        for name, loop, device in loops:
            loop.stop(timeout=max(0.0, deadline - time.monotonic()))
            if loop.running:
                log.warning("gave up waiting for %s's read after %.1f s", name, STOP_JOIN_S)
            self._update(device, running=False, reading_since_ns=None, next_retry_ns=None)

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
                self.rig.note_read(device, samples)
                self.rig.on_samples(samples)
        except Exception as error:
            log.exception("delivering %s's samples", device.name)
            self.rig.event(
                Severity.ERROR,
                SubjectKind.RIG,
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
        """One scheduled poll: deliver what it yields -- and on a raise, count a failure.

        Samples yielded before a raise are delivered all the same; the raise
        counts toward the budget. Only `read` itself can put the device
        offline; delivery failures are handled in
        [delivered][flyball.rig.polling.Polling.delivered]. Only `read` is
        timed for `slow`: the delivery after it, and the rig lock it waits
        for, are not the device's.
        """
        samples: list[Sample] = []
        error: Exception | None = None
        read_s = 0.0
        try:
            if not isinstance(device, Readable):
                raise TypeError(f"{type(device).__name__} has nothing to read")
            with device.read_lock:  # not beside a fresh read of it; never the rig's lock
                started = self.rig.clock.monotonic()  # in the rig's time, as the period is
                now_ns = self.rig.clock.now_ns()
                with self._lock:
                    self._reading[device.name] = started
                    run = self._runs.get(device.name)
                period = None if run is None else run.period_s
                self._update(device, reading_since_ns=now_ns)
                watchdog = None
                if period is not None:
                    bound = hung_after_s(period)
                    watchdog = self.rig.after(
                        bound, lambda: self._hung(device, started, bound), f"hung {device.name}"
                    )
                try:
                    for sample in device.read(now_ns):
                        samples.append(sample)
                finally:
                    if watchdog is not None:
                        watchdog.cancel()
                    with self._lock:
                        self._reading.pop(device.name, None)
                    self._update(device, reading_since_ns=None)
                    read_s = self.rig.clock.monotonic() - started
                    if self.rig.conditions.get(device, Code.HUNG) is not None:
                        self.rig.conditions.clear(
                            device, Code.HUNG, message=f"the read returned after {read_s:.1f} s"
                        )
        except Exception as e:
            error = e
        if not self._polled(device):
            return  # removed while it was being read: what it read goes nowhere
        if error is not None:
            if samples:
                self.delivered(device, samples)
            self._failed(device, error)
            return
        self.delivered(device, samples)
        self._recovered(device)
        self._paced(device, read_s)

    def _hung(self, device: Device, started: float, bound_s: float) -> None:
        """The watchdog of a read began at `started`: if that read is still in flight, `hung`."""
        with self._lock:
            if not self._polled(device) or self._reading.get(device.name) != started:
                return  # it returned; or another read is in flight, with its own watchdog
        reading_s = self.rig.clock.monotonic() - started
        raised = self.rig.conditions.set(
            device,
            Code.HUNG,
            Severity.ERROR,
            f"a read has been in flight for {reading_s:.1f} s, past {bound_s:g} s: stuck in its"
            " driver",
            {"reading_s": reading_s, "bound_s": bound_s},
        )
        if raised:
            self.rig.device_hung(device)

    def _failed(self, device: Device, error: Exception) -> None:
        """Count a read that raised; at the budget hold `offline` and back off, or give up."""
        message = f"{type(error).__name__}: {error}"
        with self._lock:
            if (run := self._runs.get(device.name)) is None or not self._polled(device):
                return
            policy = self._policies.get(device.name, self.defaults)
            failures = run.consecutive_failures + 1
            loop = self.periodic.get(device.name)
            self._update(device, consecutive_failures=failures)
        if failures < policy.fail_after:
            log.warning(
                "%s: read failed (%d of %d in a row before offline): %s",
                device.name,
                failures,
                policy.fail_after,
                message,
            )
            return
        details = {"consecutive_failures": failures}
        if self.rig.conditions.set(device, Code.OFFLINE, Severity.ERROR, message, details):
            self.rig.device_offline(device)  # what it read is stale(device_offline) at once
        if loop is None or not loop.running or not loop.runs_here():
            return  # stopped, or a newer loop a restart started: that one keeps its own time
        held = self.rig.conditions.get(device, Code.OFFLINE)
        now_ns = self.rig.clock.now_ns()
        give_up = policy.give_up_after_s
        if give_up is not None and held is not None and now_ns - held.since_ns >= give_up * 1e9:
            loop.stop(join=False)  # from inside the loop: it exits after this call
            self._update(device, running=False, next_retry_ns=None)
            self.rig.event(
                Severity.ERROR,
                SubjectKind.DEVICE,
                device.name,
                Code.GAVE_UP,
                f"offline for {(now_ns - held.since_ns) / 1e9:.0f} s, past give_up_after_s"
                f" {give_up:g}: polling stopped; restart it to read again",
                {"consecutive_failures": failures, "give_up_after_s": give_up},
            )
            return
        wait = policy.backoff(failures - policy.fail_after)
        loop.defer(wait)
        self._update(device, next_retry_ns=now_ns + round(wait * 1e9))

    def _recovered(self, device: Device) -> None:
        """After a read that succeeded: the count starts again, and `offline` clears."""
        run = self._runs.get(device.name)
        failures = 0 if run is None else run.consecutive_failures
        if failures or (run is not None and run.next_retry_ns is not None):
            self._update(device, consecutive_failures=0, next_retry_ns=None)
        if failures or self.rig.conditions.get(device, Code.OFFLINE) is not None:
            self.rig.conditions.clear(
                device,
                Code.OFFLINE,
                {"consecutive_failures": failures},
                message=f"read again after {failures} failed in a row",
            )

    def _paced(self, device: Device, read_s: float) -> None:
        """Note how long a read took; raise `slow` on a run of slow reads, clear it on fast ones.

        Raised after `SLOW_AFTER` reads in a row over the period; cleared
        after `FAST_AFTER` in a row at or under `FAST_FRACTION` of it. A read
        between the two starts both counts again. So a read that is slow now
        and then raises nothing, and one that hovers at its period does not
        flicker: one `raised` and one `cleared` per spell, whatever it does
        in between.
        """
        with self._lock:
            if not self._polled(device) or (run := self._runs.get(device.name)) is None:
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
        """Note a change to `device`'s run; a no-op once it is no longer polled.

        Checked and noted in one hold of the lock: a `stop` between the two would
        otherwise have its run put back, a device polled no more.
        """
        with self._lock:
            if not self._polled(device) or (run := self._runs.get(device.name)) is None:
                return
            run = replace(run, **changes)
            self._runs[device.name] = run
            if self.runs.watched:
                self.runs.set(device.name, run)
