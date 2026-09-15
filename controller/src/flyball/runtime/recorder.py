"""Writes deliveries to a session.

Not an observer: it sees the whole delivery after the loops have ticked, so it
records what each tick produced. The rig holds at most one and calls it last.

Deliveries are buffered and written in one transaction every `flush_s`, since
a transaction costs milliseconds on an SD card regardless of size. `close`
writes what is left.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from typing import Any

from flyball.control import Loop
from flyball.core.device import Event
from flyball.core.reading import Channel, Reading, Sample, Source
from flyball.db import SessionWriter, Tick
from flyball.db.types import Event as StoredEvent


def _tick(loop: Loop[Any], reading: Reading, start_ns: int) -> Tick:
    """The loop's state after ticking on `reading`, as a row."""
    return Tick(
        loop=loop.name,
        offset_ns=reading.time_ns - start_ns,
        mode=loop.mode.value,
        correction=loop.correction,
        reading=reading.value,
        setpoint=None if loop.reference is None else loop.setpoint_at(reading.time_ns),
        demand=loop.demand,
        expected=loop.expected,
        delivered_correction=loop.delivered_correction,
    )


class Recorder:
    """Records the given sources and loops into one session until closed."""

    __slots__ = (
        "_last_flush",
        "_samples",
        "_start_ns",
        "_ticks",
        "flush_s",
        "loops",
        "sources",
        "writer",
    )

    def __init__(
        self,
        writer: SessionWriter,
        sources: Iterable[Source],
        loops: Iterable[tuple[Channel, Loop[Any]]] = (),
        flush_s: float = 0.1,
    ) -> None:
        self.writer = writer
        self.flush_s = flush_s
        self._samples: list[Sample] = []
        self._ticks: list[Tick] = []
        self._last_flush = time.monotonic()
        self.sources = frozenset(sources)
        loops = tuple(loops)
        self.loops = frozenset(loop for _, loop in loops)
        self._start_ns = writer.session.start_ns
        # A loop's controlled variable is always recorded, asked for or not.
        self.sources |= {channel.source for channel, _ in loops}
        for source in self.sources:
            writer.declare_source(source)
        for channel, loop in loops:
            writer.declare_actuator(loop.actuator.name, type(loop.actuator).__name__)
            writer.declare_loop(
                loop.name,
                channel,
                None if loop.law is None else loop.law.config.model_dump(mode="json"),
            )

    def record(
        self, samples: Sequence[Sample], ticked: Sequence[tuple[Loop[Any], Reading]]
    ) -> None:
        """One delivery, buffered. Unrecorded sources and loops are skipped."""
        self._samples.extend(s for s in samples if s.source in self.sources)
        self._ticks.extend(
            _tick(loop, reading, self._start_ns) for loop, reading in ticked if loop in self.loops
        )
        if time.monotonic() - self._last_flush >= self.flush_s:
            self.flush()

    def event(self, event: Event) -> None:
        """Write an event now: they are rare, and a log entry should not lag its cause."""
        self.writer.write_event(
            StoredEvent(
                event.time_ns - self._start_ns,
                event.kind,
                event.subject,
                {
                    "level": int(event.level),
                    "scope": event.scope,
                    "message": event.message,
                    "details": event.details,
                },
            )
        )

    def flush(self) -> None:
        """Write everything buffered, in one transaction per table."""
        self._last_flush = time.monotonic()
        if self._samples:
            self.writer.write_samples(self._samples)
            self._samples = []
        if self._ticks:
            self.writer.write_ticks(self._ticks)
            self._ticks = []

    def close(self, end_ns: int) -> None:
        self.flush()
        self.writer.end(end_ns)
