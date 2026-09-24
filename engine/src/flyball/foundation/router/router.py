"""The router: every current reading, by signal, and every latest sample, by node.

The one place values live. A device holds no copies: it reads the router
when it needs a value (`signal.value` in `commit`) and pushes to it when it
has one (`signal.push(value)`). The rig hooks `deliver` so a push runs a
delivery -- observers, controllers, commits, the recorder -- and every
device the rig holds shares the rig's router. A device on its own (a test)
has one of its own, where a push is simply noted.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING

from ..errors import NotReadyError

if TYPE_CHECKING:
    from ..device.signal import Node, Reading, Sample, Signal, Value

RECENT_READINGS = 60
"""How many readings the router keeps per signal, for a stat on request: noise, rate."""


class Router:
    latest: dict[Signal, Reading]
    """The newest reading on each signal."""
    samples: dict[Node, Sample]
    """The newest sample delivered on each node."""
    cuts: dict[Node, Sample]
    """The newest sample delivered on another node of the tree, cut down to this atomic one: a
    root sample carrying `dry.humidity` is the newest instant on `dry` too."""
    recent: dict[Signal, deque[Reading]]
    last_usable: dict[Signal, Reading]
    """The newest reading on each signal that had a value: what `latest` was before a no-value."""
    seq: dict[Signal, int]
    """How many readings each signal has had: what changed, when the clock did not move."""
    deliver: Callable[[Sequence[Sample]], None] | None
    """What a push runs: the rig's delivery once attached; None notes the samples and no more."""
    now_ns: Callable[[], int]
    """The clock a push without a time stamps with: the rig's once attached."""

    def __init__(self) -> None:
        self.latest = {}
        self.samples = {}
        self.cuts = {}
        self.recent = {}
        self.last_usable = {}
        self.seq = {}
        self.deliver = None
        self.now_ns = time.time_ns

    # region Get

    def reading(self, signal: Signal) -> Reading | None:
        """The newest reading on `signal`; None before the first."""
        return self.latest.get(signal)

    def value(self, signal: Signal) -> Value:
        """The newest value on `signal`.

        Raises:
            NotReadyError: Nothing has been read on it yet.
            NoValueError: The newest reading has no value (`invalid`, `stale`, ...); a
                `NotReadyError` too. Nothing substitutes the last one.
        """
        from ..device.novalue import NoValue, NoValueError

        if (reading := self.latest.get(signal)) is None:
            raise NotReadyError(f"Nothing has been read on '{signal.address}' yet")
        if isinstance(value := reading.value, NoValue):
            raise NoValueError(signal.address, value)
        return value

    def sample(self, node: Node) -> Sample | None:
        """The newest instant on `node`: delivered on it, or cut to it if atomic; else None."""
        known = [s for s in (self.samples.get(node), self.cuts.get(node)) if s is not None]
        return max(known, key=lambda s: s.time_ns) if known else None  # a tie: the one on it

    def samples_under(self, node: Node) -> Iterator[Sample]:
        """The newest sample on `node` and on each namespace under it, where there is one."""
        for n in (node, *node.descendants()):
            if (sample := self.samples.get(n)) is not None:
                yield sample

    def recent_readings(self, signal: Signal, n: int = RECENT_READINGS) -> list[Reading]:
        """The last `n` readings on `signal`, oldest first: a copy, so compute on it unlocked."""
        recent = self.recent.get(signal)
        return [] if recent is None else list(recent)[-n:]

    # endregion

    # region Put

    def note(self, sample: Sample, received_ns: int | None = None) -> list[Node]:
        """Record a delivered sample: `latest`, `recent`, `samples`, the atomic nodes it cuts to.

        `received_ns` is stamped on each reading: when the rig took delivery.
        Returns the atomic nodes above its signals other than its own, whose
        cut is now this sample's; the caller decides what else the delivery does.
        """
        self.samples[sample.node] = sample
        cuts: dict[Node, None] = {}
        for reading in sample.readings(received_ns):
            signal = reading.signal
            self.latest[signal] = reading
            if reading.usable:
                self.last_usable[signal] = reading
            self.seq[signal] = self.seq.get(signal, 0) + 1
            if (recent := self.recent.get(signal)) is None:
                recent = self.recent[signal] = deque(maxlen=RECENT_READINGS)
            recent.append(reading)
            node: Node | None = signal.node
            while node is not None:
                if node.atomic and node is not sample.node:
                    cuts[node] = None
                node = node.parent
        for node in cuts:
            if (cut := sample.under(node)) is not None:
                self.cuts[node] = cut
        return list(cuts)

    def push(self, samples: Sample | Sequence[Sample]) -> None:
        """Deliver `samples`: through the rig if attached, else noted here."""
        from ..device.signal import Sample

        batch = (samples,) if isinstance(samples, Sample) else samples
        if self.deliver is not None:
            self.deliver(batch)  # the rig puts them through the value gate
        else:
            from ..device.signal import normalised

            for sample in batch:
                self.note(normalised(sample), self.now_ns())

    def push_reading(self, signal: Signal, value: Value, time_ns: int | None = None) -> None:
        """One value on one signal, as a sample on its node, delivered."""
        from ..device.signal import Sample

        at = self.now_ns() if time_ns is None else time_ns
        self.push(Sample(signal.node, at, {signal: value}))

    # endregion


__all__ = ["RECENT_READINGS", "Router"]
