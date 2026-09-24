"""Fault time, accrued on the rig clock: when a controller's source has been faulty long enough.

A5: a controller whose measured signal is fault-class (`invalid`, `stale` for any
reason) freezes at once; what it does about it (`on_fault`, a later stage) is
released only once the fault time it has **accrued** reaches its wait, and only
by a one-shot on the rig clock -- a silent or offline source delivers nothing,
so no delivery could release it.

- An **outage** starts at the source's first fault-class reading (a driver's
  `invalid`, the rig's `stale` -- the liveness timer's, a device going offline or
  hung, an echo demand whose writes fail), and continues through changes of
  reason. It ends after 3 readings in a row with a value, which also resets the
  accrued time.
- Time **accrues** only while the source is fault-class: from each reading into
  fault-class until the next reading that is not (a value, or a benign
  `not_applicable`). Such a reading pauses the accrual, never resets it.
- The **wait** is keyed on the reason: `max(2·poll_s, 1 s)` for a single
  observation (`invalid`, `stale(device_offline | device_hung | write_failed)`),
  from the source's own `poll_s`, or for a push source `2·min_period_s`, else
  1 s; none for staleness by age (`silent`, `never_read`, `last_read`: its
  `stale_after_s` was the grace). A law that raises is released at once.
- The **release** comes once per outage, and only while the controller is
  REGULATING (one in MANUAL keeps accruing; `regulate` looks again): a
  one-shot armed on each entry into fault-class for the wait still to go,
  cancelled by the next reading that is not fault-class. Its call takes the
  rig's lock and checks the quality and the accrued time again before it
  releases.

What a release does is the rig's `on_fault` hook: nothing, until the `on_fault`
actions exist. The hook is where they plug in.

[Accrual][flyball.rig.faults.Accrual] is the arithmetic alone, shared with
[Bands][flyball.rig.bands.Bands] for A4's `invalid` grace.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from flyball.foundation.device import NoValue, Quality, Reading, Reason, Signal

if TYPE_CHECKING:
    from flyball.foundation.time import Timer
    from flyball.model.controller import Controller

    from .rig import Rig

log = logging.getLogger("flyball.faults")

EPISODE_ENDS_AFTER = 3
"""Readings with a value, in a row, that end an outage (and a band's no-value episode)."""
WAIT_MIN_S = 1.0
"""The least wait for a single-observation fault: `max(2·poll_s, WAIT_MIN_S)`."""

BY_AGE = frozenset({Reason.SILENT, Reason.NEVER_READ, Reason.LAST_READ})
"""Stale reasons whose grace was the threshold itself: no added wait."""

LAW_ERROR = "law_error"
"""The reason a release gives for a law that raised."""


def fault_of(reading: Reading) -> NoValue | None:
    """The fault-class no-value `reading` carries (`invalid`, `stale`), or None."""
    value = reading.value
    if isinstance(value, NoValue) and value.quality.fault:
        return value
    return None


def wait_s(signal: Signal, fault: NoValue, min_period_s: float | None = None) -> float:
    """How much fault time `fault` on `signal` accrues before its outage is released."""
    if fault.quality is Quality.STALE and fault.reason in BY_AGE:
        return 0.0
    poll_s = signal.poll_s
    if poll_s is None:
        poll_s = 2 * min_period_s if min_period_s is not None else WAIT_MIN_S
    return max(2 * poll_s, WAIT_MIN_S)


class Accrual:
    """Fault time accrued over one episode: paused by a good reading, reset by enough of them.

    The arithmetic of A5, without a clock or a timer of its own: the owner
    says what arrived and when, and asks what is left.
    """

    __slots__ = ("accrued_ns", "fresh", "released", "since_ns")

    def __init__(self) -> None:
        self.accrued_ns = 0
        """Fault time accrued before the current spell."""
        self.since_ns: int | None = None
        """When the current fault-class spell began; None: not fault-class now."""
        self.fresh = 0
        """Readings with a value in a row since the last fault."""
        self.released = False
        """Whether this episode has been released: once per episode."""

    @property
    def faulty(self) -> bool:
        return self.since_ns is not None

    def total_ns(self, now_ns: int) -> int:
        """Fault time accrued in this episode up to `now_ns`."""
        spell = 0 if self.since_ns is None else max(0, now_ns - self.since_ns)
        return self.accrued_ns + spell

    def fault(self, now_ns: int) -> None:
        """A fault-class reading: a spell starts, or goes on."""
        self.fresh = 0
        if self.since_ns is None:
            self.since_ns = now_ns

    def pause(self, now_ns: int) -> None:
        """A reading that is not fault-class: the spell ends, its time is kept."""
        if self.since_ns is not None:
            self.accrued_ns += max(0, now_ns - self.since_ns)
            self.since_ns = None

    def usable(self, now_ns: int) -> bool:
        """A reading with a value: pause, and count it. Whether it ended the episode."""
        self.pause(now_ns)
        self.fresh += 1
        return self.fresh >= EPISODE_ENDS_AFTER

    def remaining_ns(self, now_ns: int, wait_ns: int) -> int:
        """Fault time still to accrue before the wait is reached; 0 or less: reached."""
        return wait_ns - self.total_ns(now_ns)


@dataclass(slots=True, eq=False)
class Outage:
    """A controller's source in an outage: its accrual, the reason now, the one-shot armed."""

    controller: Controller
    accrual: Accrual
    reason: str = ""
    """The latest fault's quality and reason: `invalid`, `stale(silent)`, `law_error`."""
    started_ns: int = 0
    wait_ns: int = 0
    timer: Timer | None = None
    released_ns: int | None = None


type OnFault = Callable[[Outage], None]


class Faults:
    """Every regulated source's outage, kept from its deliveries and released on the rig clock.

    Under the rig's lock: evaluated on each delivery to a controller, and by
    the one-shot, which takes it.
    """

    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self.outages: dict[Controller, Outage] = {}
        self.on_fault: list[OnFault] = []
        """Called with the outage when one is released: where `on_fault`'s actions plug in."""

    def delivered(self, controller: Controller, reading: Reading) -> None:
        """A reading of `controller`'s source was delivered: start, pause, end or go on."""
        now = self.rig.clock.now_ns()
        fault = fault_of(reading)
        outage = self.outages.get(controller)
        if fault is None:
            if outage is None:
                return
            self._cancel(outage)
            accrual = outage.accrual
            if reading.usable:
                if accrual.usable(now):
                    del self.outages[controller]  # the outage is over; the time resets
            else:
                accrual.pause(now)  # benign: no time accrues, and the run of values restarts
                accrual.fresh = 0
            return
        if outage is None:
            outage = self.outages[controller] = Outage(controller, Accrual(), started_ns=now)
        outage.accrual.fault(now)
        outage.reason = _describe(fault)
        outage.wait_ns = round(
            wait_s(controller.measured_signal, fault, controller.min_period_s) * 1e9
        )
        self._evaluate(outage, now)

    def law_failed(self, controller: Controller, message: str) -> None:
        """The law raised: fault-class, released at once (A5: a law error waits for nothing)."""
        now = self.rig.clock.now_ns()
        outage = self.outages.get(controller)
        if outage is None:
            outage = self.outages[controller] = Outage(controller, Accrual(), started_ns=now)
        outage.accrual.fault(now)
        outage.reason = f"{LAW_ERROR}: {message}"
        outage.wait_ns = 0
        self._evaluate(outage, now)

    def regulating(self, controller: Controller) -> None:
        """`controller` regulates again (or its reference changed): release a wait it reached."""
        if (outage := self.outages.get(controller)) is not None:
            self._evaluate(outage, self.rig.clock.now_ns())

    def forget(self, controller: Controller) -> None:
        """`controller` is detached: its outage goes with it."""
        if (outage := self.outages.pop(controller, None)) is not None:
            self._cancel(outage)

    def accrued_s(self, controller: Controller) -> float:
        """How much fault time `controller`'s source has accrued in its outage now; 0: none."""
        outage = self.outages.get(controller)
        return 0.0 if outage is None else outage.accrual.total_ns(self.rig.clock.now_ns()) / 1e9

    def _evaluate(self, outage: Outage, now_ns: int) -> None:
        """Release now if the wait is reached; else arm the one-shot for what is left."""
        self._cancel(outage)
        accrual = outage.accrual
        if accrual.released or not accrual.faulty:
            return
        left = accrual.remaining_ns(now_ns, outage.wait_ns)
        if left <= 0:
            if outage.controller.mode.active():
                self._release(outage, now_ns)
            return
        outage.timer = self.rig.after(
            left / 1e9, lambda: self._due(outage), f"fault wait {outage.controller.name}"
        )

    def _due(self, outage: Outage) -> None:
        with self.rig.lock:
            if self.outages.get(outage.controller) is not outage:
                return  # ended, or the controller is gone
            outage.timer = None
            self._evaluate(outage, self.rig.clock.now_ns())

    def _release(self, outage: Outage, now_ns: int) -> None:
        outage.accrual.released = True
        outage.released_ns = now_ns
        log.info(
            "controller %s: source faulty for %.3f s (%s): released",
            outage.controller.name,
            outage.accrual.total_ns(now_ns) / 1e9,
            outage.reason,
        )
        for hook in list(self.on_fault):
            try:
                hook(outage)
            except Exception:
                log.exception("on_fault hook for %s", outage.controller.name)

    @staticmethod
    def _cancel(outage: Outage) -> None:
        if outage.timer is not None:
            outage.timer.cancel()
            outage.timer = None

    def close(self) -> None:
        for outage in self.outages.values():
            self._cancel(outage)


def _describe(fault: NoValue) -> str:
    return f"{fault.quality.value}({fault.reason})" if fault.reason else fault.quality.value


__all__ = [
    "EPISODE_ENDS_AFTER",
    "Accrual",
    "Faults",
    "Outage",
    "fault_of",
    "wait_s",
]
