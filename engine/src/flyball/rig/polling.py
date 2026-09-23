"""Runs each device's `read` on its period and keeps what the runtime knows of the run.

A device is polled at the smallest `poll_s` in its tree: the device's own,
inherited down, or any namespace's or signal's override. Which signals are
due at a given call is the driver's business inside `read`; the runtime
only knocks often enough. What the runtime knows -- period, last delivery,
whether the device is stopped on an error -- lives here, beside the
device's own state, and is pushed through `runs`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from flyball.foundation.device import Condition, Device, Level, Readable, Sample
from flyball.foundation.errors import NotFoundError
from flyball.foundation.router import Latest
from flyball.foundation.time import PeriodicLoop

log = logging.getLogger("flyball.polling")

if TYPE_CHECKING:
    from .rig import Rig


def poll_period(device: Device) -> float | None:
    """The period the runtime polls `device` on: the smallest over its publishing signals.

    Each signal's `poll_s` is already the nearest one up the tree, so the
    device's own and any namespace override are counted through it. None
    when nothing publishes on a period: the device is never polled.
    """
    periods = [s.poll_s for s in device.publishing.values() if s.poll_s is not None]
    return min(periods) if periods else None


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceRun:
    """How the runtime is polling a device, beside what the device reports of itself."""

    period_s: float | None = None
    running: bool = False
    last_read_ns: int | None = None
    conditions: tuple[Condition, ...] = ()


class Polling:
    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self.by_name: dict[str, Device] = {}
        self.periodic: dict[str, PeriodicLoop] = {}
        self.runs: Latest[str, DeviceRun] = Latest()
        """The newest run of each polled device, by name, after every read or failure."""
        self._runs: dict[str, DeviceRun] = {}

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
        if period is not None:
            self.start(device, period)
        self._update(device, conditions=())
        self.rig.event(Level.INFO, "device", name, "restarted", "polling again")
        return self._runs[name]

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
        self.runs.discard(name)

    def stop_all(self) -> None:
        for name, loop in self.periodic.items():
            loop.stop()
            self._update(self.by_name[name], running=False)

    def delivered(self, device: Device, samples: Sequence[Sample]) -> None:
        """Samples `device` read: into the rig, then noted as its latest.

        A failure *downstream* -- a law, an observer, a driver's `commit` --
        is the rig's, not the read's: it becomes an event and the device
        carries on, its samples still noted as read.
        """
        try:
            with self.rig.lock:
                if not self._polled(device):
                    return  # removed while it was being read
                self.rig.on_samples(samples)
        except Exception as error:
            log.exception("delivering %s's samples", device.name)
            self.rig.event(
                Level.ERROR,
                "rig",
                device.name,
                "delivery_failed",
                f"{type(error).__name__}: {error}",
                {"device": device.name},
            )
        if (run := self._runs.get(device.name)) is None:
            return
        last_read_ns = max(s.time_ns for s in samples) if samples else run.last_read_ns
        self._update(device, last_read_ns=last_read_ns, conditions=())

    def _read(self, device: Device) -> None:
        """One scheduled poll: deliver what it returns -- or note the failure and stop polling.

        Only `read` itself can put the device offline; delivery failures are
        handled in [delivered][flyball.rig.polling.Polling.delivered].
        A device that went offline stays stopped until `restart`.
        """
        started = self.rig.clock.monotonic()  # in the rig's time, as the period is
        try:
            if not isinstance(device, Readable):
                raise TypeError(f"{type(device).__name__} has nothing to read")
            samples = tuple(device.read(self.rig.clock.now_ns()))
        except Exception as error:
            if not self._polled(device):
                return  # removed while it was being read: nothing to put offline
            offline = Condition(
                "offline", Level.ERROR, f"{type(error).__name__}: {error}", self.rig.clock.now_ns()
            )
            self._update(device, running=False, conditions=(offline,))
            self.rig.event(Level.ERROR, "device", device.name, "offline", offline.message)
            if (loop := self.periodic.get(device.name)) is not None:
                loop.stop(join=False)  # from inside the loop: it exits after this call
            return
        if not self._polled(device):
            return  # removed while it was being read: what it read goes nowhere
        self.delivered(device, samples)
        if (run := self._runs.get(device.name)) is None:
            return
        period = run.period_s
        took = self.rig.clock.monotonic() - started
        if period is not None and took > period:
            slow = Condition(
                "slow",
                Level.WARNING,
                f"read took {took:.2f} s against a {period} s period",
                self.rig.clock.now_ns(),
            )
            self._update(device, conditions=(slow,))
            self.rig.event(Level.WARNING, "device", device.name, "slow", slow.message)

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
