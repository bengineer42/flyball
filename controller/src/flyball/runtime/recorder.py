"""Writes deliveries to a session.

Not an observer: it wants the whole delivery, after the loops have ticked, in
one transaction -- so it sees the demand and expected value each tick
produced, which no observer can. The rig holds at most one and calls it last.
Which sources and loops are recorded is decided here, once, at construction.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from flyball.control import Loop
from flyball.core.reading import Channel, Reading, Sample, Source
from flyball.db import SessionWriter, Tick


def _tick(loop: Loop[Any], reading: Reading, start_ns: int) -> Tick:
    """The loop's state after ticking on ``reading``, as a row."""
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

    __slots__ = ("_start_ns", "loops", "sources", "writer")

    def __init__(
        self,
        writer: SessionWriter,
        sources: Iterable[Source],
        loops: Iterable[tuple[Channel, Loop[Any]]] = (),
    ) -> None:
        self.writer = writer
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
        """One delivery. Unrecorded sources and loops are skipped."""
        self.writer.write_samples(s for s in samples if s.source in self.sources)
        for loop, reading in ticked:
            if loop in self.loops:
                self.writer.write_tick(_tick(loop, reading, self._start_ns))

    def close(self, end_ns: int) -> None:
        self.writer.end(end_ns)
