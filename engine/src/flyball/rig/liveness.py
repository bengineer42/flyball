"""Liveness: a signal that stops arriving goes `stale` at the rig's threshold, on the rig clock.

A signal is judged when its reading is a measurement the rig expects on a
period: a readout, or a demand whose readback is `sensed`, that publishes.
Settings, configs and `echo` demands are not (an echo demand's liveness is its
writes': `stale(write_failed)`). Its **threshold** is its own `stale_after_s`,
else `max(3·poll_s, 5 s)` from its own effective `poll_s` while its device is
polled; a signal with neither (a push, a device nobody polls) is not judged, nor
is a record (`dtype: json`, such as `last.<command>`) without its own.

Each judged signal has one record, built when its device's polling starts (or
when it is added, for a signal with its own `stale_after_s`), and one pending
one-shot on the rig's [Timers][flyball.foundation.time.timer.Timers] at most.
An arrival -- a delivered reading of the signal, whether a value or a driver's
no-value, but never the rig's own `stale` -- only notes the time
(`received_ns`); the one-shot, when it comes up, finds the true deadline and
either re-arms for it or declares the signal stale. So a signal read every
100 ms costs a store per reading, not a timer.

At the deadline the rig pushes a `stale` reading on the signal, stamped now: the
chart breaks at the rig's threshold, a controller on it freezes, a band on it
is unknown. The reason says which:

- `never_read`: nothing has arrived since its device's first successful read
  (or since it was watched, if the device has read nothing): the `pending`
  deadline;
- `silent`: it had readings, and its device has delivered nothing within the
  threshold either;
- `last_read`: it had readings, and its device still delivers other signals,
  but not this one;
- `device_offline` / `device_hung`: its device holds that condition (a signal
  never read on a device already offline goes stale with the device's reason).

A signal whose newest reading is `not_applicable` is not judged until a value
arrives: the quantity is undefined, not late. One whose newest reading is
already `stale` waits for its next arrival. The next arrival ends it either way.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from flyball.foundation.device import (
    Access,
    Code,
    Device,
    Node,
    NoValue,
    Quality,
    Readback,
    Reading,
    Reason,
    Role,
    Sample,
    Signal,
    stale,
)

if TYPE_CHECKING:
    from flyball.foundation.time import Timer

    from .rig import Rig

log = logging.getLogger("flyball.liveness")

STALE_MIN_S = 5.0
"""The least default stale threshold: `max(3·poll_s, STALE_MIN_S)`."""
STALE_PERIODS = 3
"""How many periods of silence before a polled signal is stale, by default."""


def stale_after_s(signal: Signal, polled: bool = True) -> float | None:
    """The threshold `signal` is judged by, or None: not judged.

    Its own `stale_after_s` when set; else, while its device is `polled`,
    `max(3·poll_s, 5 s)` from its own effective `poll_s`. None for a signal
    that is not a measurement expected on a period (a setting, a config, an
    `echo` demand, a record such as `last.<command>`), or that does not publish.
    """
    if not judged(signal):
        return None
    own = signal.spec.stale_after_s
    if own is not None:
        return own
    if not polled or (poll_s := signal.poll_s) is None or signal.spec.dtype == "json":
        return None  # not read on a period; or a record (`last.<command>`), pushed on an event
    return max(STALE_PERIODS * poll_s, STALE_MIN_S)


def judged(signal: Signal) -> bool:
    """Whether `signal` is a published measurement: a readout, or a `sensed` demand."""
    if Access.P not in signal.access:
        return False
    role = signal.role
    return role is Role.READOUT or (role is Role.DEMAND and signal.spec.readback is Readback.SENSED)


def hung_after_s(period_s: float) -> float:
    """How long a poll's read may be in flight before its device is `hung`: `max(3·period, 5 s)`."""
    return max(STALE_PERIODS * period_s, STALE_MIN_S)


@dataclass(slots=True, eq=False)
class Watch:
    """One judged signal: its threshold and when it last arrived, on the rig clock."""

    signal: Signal
    threshold_ns: int
    since_ns: int
    """When it was first watched: the base of the `pending` deadline before any read."""
    arrived_ns: int | None = None
    """When a reading of it last arrived (its `received_ns`); None: never."""
    timer: Timer | None = None


@dataclass(slots=True, eq=False)
class _DeviceWatch:
    device: Device
    polled: bool
    signals: dict[Signal, Watch] = field(default_factory=dict)
    first_read_ns: int | None = None
    """When a poll of it first delivered: the `pending` deadline's base from then on."""
    arrived_ns: int | None = None
    """When anything it read last arrived."""


class Liveness:
    """Every judged signal's record, armed on the rig's timers. Changed under the rig's lock."""

    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self._devices: dict[Device, _DeviceWatch] = {}
        self.watches: dict[Signal, Watch] = {}
        """Every judged signal's record, by signal: what a delivery looks an arrival up in."""

    def threshold_s(self, signal: Signal) -> float | None:
        """The threshold `signal` is judged by now, or None: it is not judged."""
        watch = self.watches.get(signal)
        return None if watch is None else watch.threshold_ns / 1e9

    def watch(self, device: Device, *, polled: bool) -> None:
        """Judge `device`'s signals; what has arrived is kept.

        At add (`polled` False: only an own `stale_after_s`), and again when its
        polling starts or its period changes.
        """
        now = self.rig.clock.now_ns()
        held = self._devices.get(device)
        if held is None:
            held = self._devices[device] = _DeviceWatch(device, polled)
        held.polled = polled or held.polled
        for signal in device.signals.values():
            threshold = stale_after_s(signal, held.polled)
            old = held.signals.get(signal)
            if threshold is None:
                if old is not None:
                    self._drop(old)
                    del held.signals[signal]
                continue
            if old is None:
                old = held.signals[signal] = Watch(signal, round(threshold * 1e9), now)
                self.watches[signal] = old
            else:
                old.threshold_ns = round(threshold * 1e9)
            self._arm(old, now)

    def unwatch(self, device: Device) -> None:
        """Stop judging `device`'s signals: it is removed."""
        held = self._devices.pop(device, None)
        if held is None:
            return
        for watch in held.signals.values():
            self._drop(watch)

    def _drop(self, watch: Watch) -> None:
        if watch.timer is not None:
            watch.timer.cancel()
            watch.timer = None
        if self.watches.get(watch.signal) is watch:
            del self.watches[watch.signal]

    # region Arrivals: on the delivery path, under the rig's lock

    def arrived(self, reading: Reading, received_ns: int) -> None:
        """A reading of a watched signal was delivered: note it, and arm its deadline if none is."""
        value = reading.value
        if isinstance(value, NoValue) and value.quality is Quality.STALE:
            return  # the rig's own verdict, not an arrival
        watch = self.watches.get(reading.signal)
        if watch is None:
            return
        watch.arrived_ns = received_ns
        held = self._devices.get(reading.signal.device)
        if held is not None:
            held.arrived_ns = received_ns
        if watch.timer is None or not watch.timer.active:
            self._arm(watch, received_ns)

    def device_read(self, device: Device, received_ns: int, delivered: bool) -> None:
        """A poll of `device` succeeded: its first is the `pending` deadline's base.

        `delivered`: it yielded something, so the device is not silent.
        """
        held = self._devices.get(device)
        if held is not None:
            if delivered:
                held.arrived_ns = received_ns
            if held.first_read_ns is None:
                held.first_read_ns = received_ns

    # endregion

    # region Deadlines: on the timers

    def _deadline(self, watch: Watch) -> int | None:
        """When `watch` goes stale unless something arrives, on the rig's clock; None: not now."""
        latest = self.rig.router.latest.get(watch.signal)
        if (
            latest is not None
            and isinstance(latest.value, NoValue)
            and latest.value.quality in (Quality.STALE, Quality.NOT_APPLICABLE)
        ):
            return None  # already stale, or undefined now: the next arrival re-arms
        if watch.arrived_ns is not None:
            return watch.arrived_ns + watch.threshold_ns
        held = self._devices.get(watch.signal.device)
        base = watch.since_ns
        if held is not None and held.first_read_ns is not None:
            base = max(base, held.first_read_ns)
        return base + watch.threshold_ns

    def _arm(self, watch: Watch, now_ns: int) -> None:
        if watch.timer is not None:
            watch.timer.cancel()
            watch.timer = None
        due = self._deadline(watch)
        if due is None:
            return
        watch.timer = self.rig.after(
            max(0, due - now_ns) / 1e9, lambda: self._due(watch), f"stale {watch.signal.address}"
        )

    def _due(self, watch: Watch) -> None:
        """A watch's one-shot came up: re-arm for the true deadline, or declare it stale."""
        with self.rig.lock:
            if self.watches.get(watch.signal) is not watch:
                return  # dropped meanwhile
            watch.timer = None
            now = self.rig.clock.now_ns()
            due = self._deadline(watch)
            if due is None:
                return
            if due > now:
                self._arm(watch, now)
                return
            held = self._devices.get(watch.signal.device)
            siblings = [watch]
            if held is not None:  # the device's others due too: one delivery
                for other in held.signals.values():
                    if other is watch:
                        continue
                    if (at := self._deadline(other)) is not None and at <= now:
                        if other.timer is not None:
                            other.timer.cancel()
                            other.timer = None
                        siblings.append(other)
            self._declare(siblings, now)

    def _declare(self, watches: list[Watch], now_ns: int) -> None:
        """Push `stale` on each of `watches`, with the reason, as one delivery."""
        by_node: dict[Node, dict[Signal, Any]] = {}
        for watch in watches:
            signal = watch.signal
            by_node.setdefault(signal.node, {})[signal] = stale(self._reason(watch, now_ns))
        self.rig.on_samples([Sample(node, now_ns, values) for node, values in by_node.items()])

    def _reason(self, watch: Watch, now_ns: int) -> Reason:
        device = watch.signal.device
        conditions = self.rig.conditions
        if conditions.get(device, Code.OFFLINE) is not None:
            return Reason.DEVICE_OFFLINE
        if conditions.get(device, Code.HUNG) is not None:
            return Reason.DEVICE_HUNG
        if watch.arrived_ns is None:
            return Reason.NEVER_READ
        held = self._devices.get(device)
        others = None if held is None else held.arrived_ns
        if (
            others is not None
            and others > watch.arrived_ns
            and now_ns - others < watch.threshold_ns
        ):
            return Reason.LAST_READ
        return Reason.SILENT

    # endregion

    def close(self) -> None:
        for held in list(self._devices.values()):
            for watch in held.signals.values():
                if watch.timer is not None:
                    watch.timer.cancel()
                    watch.timer = None


__all__ = ["STALE_MIN_S", "Liveness", "Watch", "hung_after_s", "judged", "stale_after_s"]
