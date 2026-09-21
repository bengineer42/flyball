"""Writes deliveries to a session.

Not an observer: it sees the whole delivery after the controllers have
ticked and the touched devices have committed, so it records what each tick
produced and what each write set. The rig holds at most one and calls it last.

What is recorded follows a signal's access: readings on publishing signals
(`P` is recorded; a fresh read of a setting is for whoever asked for it),
write states on writable ones (a write to a setting is in the history as
what was set), and a controller's ticks with its source always included.

Deliveries are buffered and written in one transaction every `flush_s`, since
a transaction costs milliseconds on an SD card regardless of size. The
writing happens on the recorder's own thread: nothing on the delivery path
waits for a disk, and a disk that fails stops the recording, not the
control. `close` writes what is left.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from threading import Event as StopEvent
from threading import Lock, Thread, current_thread
from typing import TYPE_CHECKING

from flyball.foundation.device import Access, Event, Reading, Sample, Signal, WriteState
from flyball.record import SessionWriter, Tick
from flyball.record.types import Event as StoredEvent

if TYPE_CHECKING:
    from flyball.control import Controller


def _tick(controller: Controller, reading: Reading, start_ns: int) -> Tick:
    """The controller's state after ticking on `reading`, as a row."""
    return Tick(
        controller=controller.name,
        offset_ns=reading.time_ns - start_ns,
        mode=controller.mode.value,
        correction=controller.correction,
        reading=reading.value,
        setpoint=None if controller.reference is None else controller.setpoint_at(reading.time_ns),
        demand=controller.demand,
        expected=controller.expected,
        delivered_correction=controller.delivered_correction,
    )


log = logging.getLogger("flyball.recorder")


class Recorder:
    """Records the given signals and controllers into one session until closed.

    Args:
        writer: The open session.
        signals: What to record: readings of the publishing ones, write
            states of the writable ones. A controller's source and target
            are always included.
        controllers: The controllers whose ticks to record.
        flush_s: How often the writer thread writes what has accumulated.
        on_failure: Called, once, from the writer thread if a write fails;
            the recorder has stopped by then and drops what it is given.
    """

    __slots__ = (
        "_buffer",
        "_declared",
        "_events",
        "_last_flush",
        "_last_time_ns",
        "_samples",
        "_start_ns",
        "_states",
        "_stop",
        "_thread",
        "_ticks",
        "controllers",
        "failed",
        "flush_s",
        "on_failure",
        "published",
        "signals",
        "writer",
        "writes",
    )

    def __init__(
        self,
        writer: SessionWriter,
        signals: Iterable[Signal],
        controllers: Iterable[Controller] = (),
        flush_s: float = 0.1,
        on_failure: Callable[[Exception], None] | None = None,
    ) -> None:
        self.writer = writer
        self.flush_s = flush_s
        self.on_failure = on_failure
        self.failed: Exception | None = None
        self._samples: list[Sample] = []
        self._ticks: list[Tick] = []
        self._states: list[tuple[int, Mapping[Signal, WriteState]]] = []
        self._events: list[StoredEvent] = []
        self._declared: list[Signal] = []
        self._buffer = Lock()  # guards the four lists; held for appends and swaps only
        self._last_flush = time.monotonic()
        self._stop = StopEvent()
        self._thread = Thread(target=self._run, daemon=True, name="recorder")
        self._start_ns = writer.session.start_ns
        self._last_time_ns = self._start_ns
        controllers = tuple(controllers)
        self.controllers = frozenset(controllers)
        # A controller's variables are always recorded, asked for or not.
        self.signals = frozenset(signals).union(*((c.source, c.target) for c in controllers))
        self.published = frozenset(s for s in self.signals if Access.P in s.access)
        self.writes = frozenset(s for s in self.signals if Access.W in s.access)
        for signal in sorted(self.signals, key=lambda s: s.address):
            writer.declare_device(signal.device)
            writer.declare_signal(signal)
        for controller in sorted(controllers, key=lambda c: c.name):
            writer.declare_controller(controller)
        self._thread.start()

    @property
    def running(self) -> bool:
        return self._thread.is_alive() and self.failed is None

    def declare(self, signals: Iterable[Signal]) -> None:
        """Record `signals` from now on: a device added mid-session. Declared on the next flush."""
        new = [s for s in signals if s not in self.signals]
        if not new:
            return
        self.signals = self.signals.union(new)
        self.published = frozenset(s for s in self.signals if Access.P in s.access)
        self.writes = frozenset(s for s in self.signals if Access.W in s.access)
        with self._buffer:
            self._declared.extend(new)

    def record(
        self,
        samples: Sequence[Sample],
        ticks: Sequence[tuple[Controller, Reading]],
        states: Mapping[Signal, WriteState],
        *,
        time_ns: int | None = None,
    ) -> None:
        """One delivery, buffered: list appends on the delivery path, nothing more.

        `time_ns` stamps the write states: the commit's time. Without it
        they take the latest sample's, then the last time this recorder saw
        -- right inside a delivery, stale after a manual demand.
        """
        if self.failed is not None:
            return
        kept: list[Sample] = []
        for sample in samples:
            if all(signal in self.published for signal in sample.values):
                kept.append(sample)
            elif values := {s: v for s, v in sample.values.items() if s in self.published}:
                kept.append(Sample(sample.node, sample.time_ns, values))
        rows = [
            _tick(controller, reading, self._start_ns)
            for controller, reading in ticks
            if controller in self.controllers
        ]
        recorded = {s: st for s, st in states.items() if s in self.writes}
        if time_ns is None:
            time_ns = max((s.time_ns for s in samples), default=self._last_time_ns)
        self._last_time_ns = max(self._last_time_ns, time_ns)
        with self._buffer:
            self._samples.extend(kept)
            self._ticks.extend(rows)
            if recorded:
                self._states.append((time_ns - self._start_ns, recorded))

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
            declared, self._declared = self._declared, []
            samples, self._samples = self._samples, []
            ticks, self._ticks = self._ticks, []
            states, self._states = self._states, []
            events, self._events = self._events, []
        self._last_flush = time.monotonic()
        for signal in sorted(declared, key=lambda s: s.address):
            self.writer.declare_device(signal.device)
            self.writer.declare_signal(signal)
        if samples:
            self.writer.write_samples(samples)
        if ticks:
            self.writer.write_ticks(ticks)
        for offset_ns, committed in states:
            self.writer.write_states(offset_ns, committed)
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
                    self._samples, self._ticks, self._states, self._events = [], [], [], []
                    self._declared = []
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
