"""Latches: what a stop or a fault action leaves refusing writes until a person resets it.

A latch is one **cause** -- the rig stop (`stop`), or a controller's `on_fault`
action (`on_fault:<controller>`) -- and the **subjects** it holds: the rig, a
device, a signal, a controller. Causes form a set per subject: a subject is free
only when no cause holds it, and each cause is cleared only by its own Reset.

What each holds, and what it refuses:

- **rig stop**: holds every device with demands but a `driver: values` one. It
  refuses automatic writes (a controller, a program, a trigger, an agent), a bound
  input's commit and `regulate`; a person's write under `operate` goes through,
  logged, and the latch stays.
- **`on_fault: manual`**: holds the controller; refuses its `regulate`.
- **`on_fault: stop`**: holds the controller and its output signal (its device,
  where a command stops it); refuses every write to them, and `regulate`.
- **`on_fault: stop_device`**: holds the controller and its output's device;
  refuses every write to the device, and `regulate`.

Reset needs `operate` and a person; it clears the latch and resumes nothing.
A latch is kept in the store once the runner attaches one, so a restart does
not un-stop a stopped rig: the rig stop's latch re-applies the stop at start.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from threading import Lock
from typing import TYPE_CHECKING, Any, Literal, cast

from flyball.foundation.device import Device, Signal
from flyball.foundation.device.values import Values

if TYPE_CHECKING:
    from flyball.model.controller import Controller
    from flyball.record.store import Store

    from .rig import Rig

log = logging.getLogger("flyball.stop")

type Kind = Literal["rig", "device", "signal", "controller"]

RIG_STOP = "stop"
"""The cause of the rig stop's latch."""


def fault_cause(controller: str) -> str:
    """The cause of a controller's `on_fault` latch."""
    return f"on_fault:{controller}"


@dataclass(frozen=True, slots=True)
class Subject:
    """What a latch holds: its kind and its name (a device's, a signal's address, ...)."""

    subject_kind: Kind
    subject: str


@dataclass(frozen=True, slots=True)
class Latch:
    """One cause, the subjects it holds, and who set it when and why."""

    cause: str
    subjects: tuple[Subject, ...]
    by: str
    """Who: the principal's `sub` for a stop, `on_fault` for a fault action."""
    at_ns: int
    """Wall-clock time it was set, ns since the epoch."""
    reason: str = ""
    action: str = ""
    """For a fault: the action that set it (`manual`, `stop`, `stop_device`)."""

    def holds(self, subject_kind: Kind, subject: str) -> bool:
        return Subject(subject_kind, subject) in self.subjects

    def as_dict(self) -> dict[str, Any]:
        return {
            "cause": self.cause,
            "subjects": [asdict(s) for s in self.subjects],
            "by": self.by,
            "at_ns": self.at_ns,
            "reason": self.reason,
            "action": self.action,
        }

    def rows(self) -> list[dict[str, str]]:
        """`[{subject_kind, subject, cause}]`: one per subject, as health lists them."""
        return [
            {"subject_kind": s.subject_kind, "subject": s.subject, "cause": self.cause}
            for s in self.subjects
        ]

    def said(self) -> str:
        """`stopped by X at T: reason`, for a refusal."""
        at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.at_ns / 1e9))
        what = "stopped" if self.cause == RIG_STOP else f"latched by {self.cause} ({self.action})"
        why = f": {self.reason}" if self.reason else ""
        return f"{what} by {self.by} at {at}{why}"


class Latches:
    """Every cause held now, with its subjects; its own lock, so health reads it lock-free.

    `on_change` hooks hear each latch set (True) or cleared (False), under the
    rig's lock when the rig set it: the programmer interrupts a program that
    names what a fault latched.
    """

    def __init__(self, rig: Rig) -> None:
        self._rig = rig
        self._lock = Lock()
        self._held: dict[str, Latch] = {}
        self.store: Store | None = None
        self.on_change: list[Callable[[Latch, bool], None]] = []

    # region Reading

    def any(self) -> bool:
        """Whether any latch holds: the write path's quick test, without the lock."""
        return bool(self._held)

    def all(self) -> list[Latch]:
        with self._lock:
            return list(self._held.values())

    def get(self, cause: str) -> Latch | None:
        with self._lock:
            return self._held.get(cause)

    @property
    def rig_stop(self) -> Latch | None:
        """The rig stop's latch, if it holds."""
        return self.get(RIG_STOP)

    def rows(self) -> list[dict[str, str]]:
        return [row for latch in self.all() for row in latch.rows()]

    def of_device(self, device: Device) -> list[Latch]:
        """What holds `device` whole: the rig stop (a device with demands), a device latch."""
        found: list[Latch] = []
        for latch in self.all():
            if latch.cause == RIG_STOP:
                if stoppable(device):
                    found.append(latch)
            elif latch.holds("device", device.name):
                found.append(latch)
        return found

    def of_signal(self, signal: Signal) -> list[Latch]:
        """What holds `signal`: its device's latches, and any on the signal itself."""
        found = self.of_device(signal.device)
        found.extend(
            latch
            for latch in self.all()
            if latch.cause != RIG_STOP and latch.holds("signal", signal.address)
        )
        return found

    def of_controller(self, controller: Controller) -> list[Latch]:
        """What refuses `controller`'s `regulate`: on it, or on its output (signal, device)."""
        found = self.of_signal(controller.output_signal)
        found.extend(
            latch
            for latch in self.all()
            if latch.holds("controller", controller.name) and latch not in found
        )
        return found

    def signals_held(self, device: Device) -> set[Signal]:
        """`device`'s signals a signal latch holds (the device itself not held whole)."""
        held = {
            s.subject for latch in self.all() for s in latch.subjects if s.subject_kind == "signal"
        }
        return {signal for signal in device.signals.values() if signal.address in held}

    # endregion

    # region Changing

    def set(self, latch: Latch) -> bool:
        """Hold `latch`; whether it is new (a cause already held keeps its first setting)."""
        with self._lock:
            if latch.cause in self._held:
                return False
            self._held[latch.cause] = latch
        self._kept(latch)
        for hook in list(self.on_change):
            try:
                hook(latch, True)
            except Exception:
                log.exception("latch hook for %s", latch.cause)
        return True

    def clear(self, cause: str) -> Latch | None:
        """Let go of `cause`; what was held, or None."""
        with self._lock:
            latch = self._held.pop(cause, None)
        if latch is None:
            return None
        self._forgotten(latch)
        for hook in list(self.on_change):
            try:
                hook(latch, False)
            except Exception:
                log.exception("latch hook for %s", latch.cause)
        return latch

    def forget_controller(self, name: str) -> list[Latch]:
        """A controller detached: its fault latches go with it."""
        return [latch for latch in (self.clear(fault_cause(name)),) if latch is not None]

    # endregion

    # region Store

    def attach(self, store: Store) -> list[Latch]:
        """Keep latches in `store` from now on; what it kept from an earlier run, not yet held."""
        self.store = store
        try:
            rows = store.latches()
        except Exception:
            log.exception("latches: the store could not be read; nothing restored")
            return []
        restored: list[Latch] = []
        for row in rows:
            latch = Latch(
                cause=row.cause,
                subjects=tuple(
                    Subject(cast("Kind", s["subject_kind"]), s["subject"]) for s in row.subjects
                ),
                by=row.by,
                at_ns=row.at_ns,
                reason=row.reason,
                action=row.action,
            )
            restored.append(latch)
        return restored

    def _kept(self, latch: Latch) -> None:
        if (store := self.store) is None:
            return
        from flyball.record.types import LatchRow

        try:
            store.put_latch(
                LatchRow(
                    cause=latch.cause,
                    subjects=[asdict(s) for s in latch.subjects],
                    by=latch.by,
                    at_ns=latch.at_ns,
                    reason=latch.reason,
                    action=latch.action,
                )
            )
        except Exception:
            log.exception("latch %s: not kept in the store", latch.cause)

    def _forgotten(self, latch: Latch) -> None:
        if (store := self.store) is None:
            return
        try:
            store.delete_latch(latch.cause)
        except Exception:
            log.exception("latch %s: not removed from the store", latch.cause)

    # endregion


def stoppable(device: Device) -> bool:
    """Whether a stop has anything to do to `device`: demands, or a stop command.

    Never a `driver: values` device, whose settings a stop leaves writable.
    """
    return (bool(device.demands) or device.stops_by() is not None) and not isinstance(
        device, Values
    )


def subjects(items: Iterable[tuple[Kind, str]]) -> tuple[Subject, ...]:
    return tuple(Subject(subject_kind, name) for subject_kind, name in items)


__all__ = ["RIG_STOP", "Latch", "Latches", "Subject", "fault_cause", "stoppable", "subjects"]
