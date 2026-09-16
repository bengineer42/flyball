"""Writes deliveries to a session.

Not an observer: it sees the whole delivery after the loops have ticked, so it
records what each tick produced. The rig holds at most one and calls it last.

Deliveries are buffered and written in one transaction every `flush_s`, since
a transaction costs milliseconds on an SD card regardless of size. The
writing happens on the recorder's own thread: nothing on the delivery path
waits for a disk, and a disk that fails stops the recording, not the
control. `close` writes what is left.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Sequence
from threading import Event as StopEvent
from threading import Lock, Thread, current_thread
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


log = logging.getLogger("flyball.recorder")


class Recorder:
    """Records the given sources and loops into one session until closed.

    Args:
        writer: The open session.
        sources: What to record; a loop's channel is always included.
        loops: The loops whose ticks to record.
        flush_s: How often the writer thread writes what has accumulated.
        on_failure: Called, once, from the writer thread if a write fails;
            the recorder has stopped by then and drops what it is given.
    """

    __slots__ = (
        "_buffer",
        "_events",
        "_last_flush",
        "_samples",
        "_start_ns",
        "_stop",
        "_thread",
        "_ticks",
        "failed",
        "flush_s",
        "loops",
        "on_failure",
        "sources",
        "writer",
    )

    def __init__(
        self,
        writer: SessionWriter,
        sources: Iterable[Source],
        loops: Iterable[tuple[Channel, Loop[Any]]] = (),
        flush_s: float = 0.1,
        on_failure: Callable[[Exception], None] | None = None,
    ) -> None:
        self.writer = writer
        self.flush_s = flush_s
        self.on_failure = on_failure
        self.failed: Exception | None = None
        self._samples: list[Sample] = []
        self._ticks: list[Tick] = []
        self._events: list[StoredEvent] = []
        self._buffer = Lock()  # guards the three lists; held for appends and swaps only
        self._last_flush = time.monotonic()
        self._stop = StopEvent()
        self._thread = Thread(target=self._run, daemon=True, name="recorder")
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
                loop.feedforward.config.model_dump(mode="json"),
            )
        self._thread.start()

    @property
    def running(self) -> bool:
        return self._thread.is_alive() and self.failed is None

    def record(
        self, samples: Sequence[Sample], ticked: Sequence[tuple[Loop[Any], Reading]]
    ) -> None:
        """One delivery, buffered: a list append on the delivery path, nothing more."""
        if self.failed is not None:
            return
        kept = [s for s in samples if s.source in self.sources]
        ticks = [
            _tick(loop, reading, self._start_ns) for loop, reading in ticked if loop in self.loops
        ]
        with self._buffer:
            self._samples.extend(kept)
            self._ticks.extend(ticks)

    def event(self, event: Event) -> None:
        """Buffer an event; it is written with the next flush, within `flush_s`."""
        if self.failed is not None:
            return
        row = StoredEvent(
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
        with self._buffer:
            self._events.append(row)

    def flush(self) -> None:
        """Write everything buffered, in one transaction per table. Safe from any thread.

        Raises:
            Exception: Whatever the store raised; the buffers taken are lost.
        """
        with self._buffer:
            samples, self._samples = self._samples, []
            ticks, self._ticks = self._ticks, []
            events, self._events = self._events, []
        self._last_flush = time.monotonic()
        if samples:
            self.writer.write_samples(samples)
        if ticks:
            self.writer.write_ticks(ticks)
        for row in events:
            self.writer.write_event(row)

    def _run(self) -> None:
        """The writer thread: flush every `flush_s` until stopped; a failure ends the recording."""
        while not self._stop.wait(self.flush_s):
            try:
                self.flush()
            except Exception as error:
                log.exception("recording stopped: the store failed")
                self.failed = error
                with self._buffer:
                    self._samples, self._ticks, self._events = [], [], []
                if self.on_failure is not None:
                    self.on_failure(error)
                return

    def close(self, end_ns: int) -> None:
        """Stop the writer, write what is left, end the session."""
        self._stop.set()
        if self._thread is not current_thread():
            self._thread.join()
        if self.failed is None:
            self.flush()
        self.writer.end(end_ns)
