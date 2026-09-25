"""The recorder: the sessions a rig's history goes into, and what writes each one.

[Recorder][flyball.runtime.recorder.Recorder] is the one owner of sessions on a
rig: it opens and ends them, keeps the scratch record and runs retention over
the store, and fills in what produced each session (`flyball_version`,
`packages`, `hardware`). Nothing else opens a session on the store for a rig.
It feeds itself from the rig through the rig's one record sink
([attach_sink][flyball.rig.rig.Rig.attach_sink]), a
[SessionRecorder][flyball.runtime.recorder.SessionRecorder] per session.

A session recorder is not an observer: it sees the whole delivery after the
controllers have ticked and the touched devices have committed, so it records
what each tick produced and what each write set. The rig calls it last.

What is recorded follows a signal's access: readings on published signals
(`P` is recorded; a fresh read of a setting is for whoever asked for it),
write states on writable ones (a write to a setting is in the history as
what was set), and a controller's ticks with its measured signal always included.

Deliveries are buffered and written in one transaction every `flush_s`, since
a transaction costs milliseconds on an SD card regardless of size. The
writing happens on the session recorder's own thread: nothing on the delivery
path waits for a disk, and a disk that fails stops the recording, not the
control. `close` writes what is left.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from functools import cache
from importlib import metadata
from threading import Event as StopEvent
from threading import Lock, RLock, Thread, current_thread
from typing import TYPE_CHECKING, Any

import flyball
from flyball.foundation.device import (
    Access,
    Code,
    Event,
    Sample,
    Severity,
    Signal,
    WriteState,
)
from flyball.record import SessionRow, SessionWriter, Store, Tick
from flyball.record.types import Event as StoredEvent
from flyball.rig.sink import Declared, Delivered, Marker, Published, Row, TickRow

from .retention import Retention

if TYPE_CHECKING:
    from flyball.model.controller import Controller
    from flyball.rig import Rig
    from flyball.runtime.config import RunnerConfig


def _offset(tick: TickRow, start_ns: int) -> Tick:
    """The rig's tick row as the session's: its time as an offset from the session's start."""
    return Tick(
        controller=tick.controller,
        offset_ns=tick.time_ns - start_ns,
        mode=tick.mode,
        correction=tick.correction,
        measured_value=tick.measured_value,
        setpoint=tick.setpoint,
        output_value=tick.output_value,
        expected=tick.expected,
        delivered_correction=tick.delivered_correction,
        reapplied=tick.reapplied,
    )


log = logging.getLogger("flyball.recorder")


class SessionRecorder:
    """Records the given signals and controllers into one session until closed.

    Args:
        writer: The open session.
        signals: What to record: readings of the published ones, write
            states of the writable ones. A controller's measured signal and output
            are always included.
        controllers: The controllers whose ticks to record.
        flush_s: How often the writer thread writes what has accumulated.
        on_failure: Called, once, from the writer thread if a write fails;
            the recorder has stopped by then and drops what it is given.
    """

    __slots__ = (
        "_buffer",
        "_controller_names",
        "_declared",
        "_events",
        "_flushing",
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
        self._flushing = Lock()  # one flush at a time: the store's writes are not safe to overlap
        self._last_flush = time.monotonic()
        self._stop = StopEvent()
        self._thread = Thread(target=self._run, daemon=True, name="recorder")
        self._start_ns = writer.session.start_ns
        self._last_time_ns = self._start_ns
        controllers = tuple(controllers)
        self.controllers = frozenset(controllers)
        self._controller_names = frozenset(c.name for c in controllers)
        # A controller's variables are always recorded, asked for or not.
        self.signals = frozenset(signals).union(
            *((c.measured_signal, c.output_signal) for c in controllers)
        )
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

    def put(self, row: Row) -> None:
        """A row of the rig's stream: a delivery, an event, a device's signals. Buffered."""
        match row:
            case Delivered():
                self.record(row.samples, row.ticks, dict(row.states), time_ns=row.time_ns)
            case Published():
                self.event(row.event)
            case Declared():
                self.declare(row.signals)
            case Marker():
                pass  # the recorder's, not a session's

    def record(
        self,
        samples: Sequence[Sample],
        ticks: Sequence[TickRow],
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
        recorded = {s: st for s, st in states.items() if s in self.writes}
        if time_ns is None:
            time_ns = max((s.time_ns for s in samples), default=self._last_time_ns)
        rows = [
            _offset(tick, self._start_ns)
            for tick in ticks
            if tick.controller in self._controller_names
        ]
        self._last_time_ns = max(self._last_time_ns, time_ns)
        # Unbounded for now: a store slower than the rig grows these until it fails. A
        # bound goes here, and a `recording_behind` condition when it is reached.
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
            event.code,
            event.subject,
            {
                "severity": str(event.severity),
                "subject_kind": event.subject_kind,
                "message": event.message,
                "details": event.details,
            },
            edge=None if event.edge is None else str(event.edge),
        )
        with self._buffer:
            self._events.append(row)

    def flush(self) -> None:
        """Write everything buffered, in one transaction per table. Safe from any thread.

        Flushes are serialised: the writer thread's and a caller's never
        overlap in the store, and each writes its buffers in the order taken.

        Raises:
            Exception: Whatever the store raised; the buffers taken are lost.
        """
        with self._flushing:
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


# region The recorder


@cache
def _packages() -> tuple[tuple[str, str], ...]:
    found: dict[str, str] = {}
    for entry in metadata.entry_points(group="flyball.configs"):
        if (dist := entry.dist) is not None:
            found[dist.metadata["Name"]] = dist.version
    return tuple(sorted(found.items()))


def installed_packages() -> dict[str, str]:
    """Each installed distribution that registers `flyball.configs`, by name: its version.

    The engine itself, `flyball-sim`, each extension and application package: what
    decides how a rig file is read and what its drivers do. Read once per process;
    what is installed does not change under a running runner.
    """
    return dict(_packages())


def rig_hardware(rig: Rig) -> Any:
    """What the rig can say about the hardware it runs on, for a session's `hardware`.

    Nothing yet -- no driver reports an identity (a serial number, a board's
    revision) -- so None. When one does, it is gathered here: the one place the
    runner's side of `hardware` comes from.
    """
    return None


def _merged(own: Any, posted: Any) -> Any:
    """The runner's value with a client's added to it; the runner's is never replaced.

    Two objects merge, the runner's keys winning; anything else is kept side by side.
    """
    if posted is None or posted == own:
        return own
    if own is None:
        return posted
    if isinstance(own, dict) and isinstance(posted, dict):
        return {**posted, **own}
    return {"rig": own, "posted": posted}


def recover(store: Store) -> None:
    """Finish what an earlier run left: a delete cut off part-way, a session never ended.

    Before anything reads the store. A session still open was left by a runner that
    died: it is ended at its last sample, or it would look live and overlap the next.
    """
    for half in store.deleting_sessions():  # a delete goes in batches: finish it
        store.delete_session(half.id)
        log.warning("finished deleting session %d, begun by an earlier run", half.id)
    for orphan in store.sessions():
        if orphan.open:
            store.end_session(orphan.id)
            log.warning("closed session %d, left open by an earlier run", orphan.id)


@dataclass(frozen=True, slots=True)
class _Switch:
    """A marker: from here on the stream goes to `to` (None: nowhere), if `was` is current.

    `was` None matches whatever is current.
    """

    to: SessionRecorder | None
    was: SessionRecorder | None = None


class Recorder:
    """The one owner of a rig's sessions: opens and ends them, keeps the scratch record.

    Every session on a rig is opened here, and each says what produced it: the
    engine's `flyball_version`, the `packages` installed, and the rig's `hardware`
    ([provenance][flyball.runtime.recorder.Recorder.provenance]). A client's posted
    `hardware` is added to the rig's, never put in its place; a posted
    `flyball_version` is not what recorded the session, so it is not kept.

    **How it is fed.** While anything is being recorded the recorder is the rig's
    record sink ([attach_sink][flyball.rig.rig.Rig.attach_sink]): the rig puts
    immutable rows, made under its lock and numbered in one order, and the
    recorder hands each to the current session's
    [SessionRecorder][flyball.runtime.recorder.SessionRecorder], which buffers
    it. A change of session is a marker in that same stream
    ([Rig.mark][flyball.rig.rig.Rig.mark]): the session to change to is opened
    and declared first, off the rig's lock; the switch is the marker; then the
    one it replaced is closed and, for `include_ns`, the scratch record's rows
    are copied over. Every row before the marker is in the old session and every
    row after it in the new, whether the stream is read at once (today) or by
    the hub's thread (D-095).

    [start][flyball.runtime.recorder.Recorder.start] with the `runner:` section
    begins the runner's housekeeping: the scratch record while nothing else is
    recorded, and the sweeps (`flyball.runtime.retention`).
    [stop][flyball.runtime.recorder.Recorder.stop] ends it and whatever is open.
    Without `start`, it records what
    [start_session][flyball.runtime.recorder.Recorder.start_session] asks for and
    nothing else, as a notebook or a test does.

    Args:
        rig: What is recorded.
        store: Where the sessions go.
    """

    def __init__(self, rig: Rig, store: Store) -> None:
        self.rig = rig
        self.store = store
        # One change of session at a time. Never held while a session recorder closes: its
        # thread may be failing, and `_failed` takes this lock.
        self._lock = RLock()
        self._session: SessionRecorder | None = None
        self._retired: list[SessionRecorder] = []
        self._applied: _Switch | None = None  # the last switch the stream carried
        self._retention: Retention | None = None
        self._keep_scratch = False

    # region What is being recorded

    @property
    def session(self) -> SessionRecorder | None:
        """What is being written now, the scratch record included; None for nothing."""
        return self._session

    @property
    def recording(self) -> SessionRecorder | None:
        """A session someone started; None under the scratch record alone, or nothing."""
        session = self._session
        return None if session is None or session.writer.session.scratch else session

    @property
    def scratch(self) -> SessionRow | None:
        """The scratch session being written now, as the store has it; None while recording."""
        session = self._session
        if session is None or not session.writer.session.scratch:
            return None
        return self.store.session(session.writer.session.id)

    def provenance(self) -> dict[str, Any]:
        """What a session opened now records as having produced it."""
        return {
            "flyball_version": flyball.__version__,
            "packages": installed_packages(),
            "hardware": rig_hardware(self.rig),
        }

    def flush(self) -> None:
        """Write everything the rig has handed over so far: for a read that needs its own rows.

        A barrier: with the hub (D-095) it will first wait for the stream to be read up to
        now; today the stream is read as it is written, so it writes the session's buffers.
        """
        if (session := self._session) is not None:
            session.flush()

    # endregion

    # region The stream

    def put(self, row: Row) -> None:
        """A row of the rig's stream, under the rig's lock: to the session, or a switch."""
        if isinstance(row, Marker):
            if isinstance(switch := row.message, _Switch):
                self._apply(switch)
            return
        if (session := self._session) is not None:
            session.put(row)

    def _switch(self, to: SessionRecorder | None, was: SessionRecorder | None = None) -> None:
        """Make `to` current at one point in the rig's stream. Under `_lock`.

        What it replaces goes to `_retired`, for the caller to close outside the lock.
        """
        if to is not None:
            self.rig.attach_sink(self)
        switch = _Switch(to, was)
        self.rig.mark(switch)
        if self._applied is not switch:  # the rig let go of the recorder (it closed): no stream
            self._apply(switch)
        if self._session is None:
            self.rig.detach_sink(self)

    def _apply(self, switch: _Switch) -> None:
        if switch.was is None or switch.was is self._session:
            if self._session is not None:
                self._retired.append(self._session)
            self._session = switch.to
        self._applied = switch

    def _close_retired(self) -> None:
        """Close what a switch replaced: its rows are all in, so its buffers are complete."""
        with self._lock:
            retired, self._retired = self._retired, []
        for session in retired:
            session.close(self.rig.clock.now_ns())

    # endregion

    # region Sessions

    def start_session(
        self,
        signals: Iterable[Signal] | None = None,
        controllers: Iterable[Controller] | None = None,
        *,
        include_ns: int | None = None,
        **fields: Any,
    ) -> SessionRecorder:
        """Open a session and record into it from the next delivery on.

        Defaults to every signal that publishes or is written, on every
        device, except those the rig file marks `record: false`, and every
        controller. Replaces what is being recorded, closing its session.
        `fields` are what the store's `open_session` takes, with the
        provenance filled in; a `start_ns` in them backdates the session. `include_ns`
        starts it that far back instead, filled from the scratch record it replaces
        (clamped to what that holds).
        """
        with self._lock:
            previous = self._session
            scratch = None
            if previous is not None and previous.writer.session.scratch and include_ns:
                scratch = previous.writer.session
                held_from = self.store.session(scratch.id).start_ns  # trimmed since it opened
                fields["start_ns"] = max(self.rig.clock.now_ns() - include_ns, held_from)
            session = self._open(signals, controllers, fields)
        self._close_retired()
        if scratch is not None:
            closed = self.store.session(scratch.id)  # ended just now, as the new one began
            opened = session.writer.session
            self.store.backfill(opened.id, scratch.id, opened.start_ns, (closed.end_ns or 0) + 1)
        return session

    def end_session(self) -> SessionRow | None:
        """End what is being recorded; the session ended, as the store has it, or None.

        While the runner keeps a scratch record, a fresh one takes over at once.
        """
        with self._lock:
            previous = self._session
            if previous is None:
                return None
            if self._keep_scratch:
                self._open(None, None, self._scratch_fields())
            else:
                self._switch(None)
        self._close_retired()
        return self.store.session(previous.writer.session.id)

    def rotate(self, older_than_ns: int) -> SessionRecorder | None:
        """Continue a recording older than `older_than_ns` in a new session; the new one.

        What it records and what it said about itself carry over; `continues` names
        the one it replaced. None when there is no recording that old.
        """
        with self._lock:
            previous = self.recording
            if previous is None:
                return None
            row = previous.writer.session
            if self.rig.clock.now_ns() - row.start_ns < older_than_ns:
                return None
            session = self._open(
                previous.signals,
                previous.controllers,
                {
                    "kind": "session",
                    "continues": row.id,
                    "config": row.config,
                    "hardware": row.hardware,
                    "details": row.details,
                    "rig_version_id": row.rig_version_id,
                    "packages": row.packages,
                },
            )
        self._close_retired()
        return session

    def ensure_scratch(self) -> SessionRecorder | None:
        """Open the scratch record if the runner keeps one and nothing is being recorded."""
        with self._lock:
            if not self._keep_scratch or self._session is not None:
                return None
            session = self._open(None, None, self._scratch_fields())
        log.info("scratch record: session %d", session.writer.session.id)
        return session

    def _scratch_fields(self) -> dict[str, Any]:
        name = self.rig.name
        return {"kind": "scratch", "config": {"name": name} if name else None}

    def _open(
        self,
        signals: Iterable[Signal] | None,
        controllers: Iterable[Controller] | None,
        fields: dict[str, Any],
    ) -> SessionRecorder:
        """Open a session with `fields`, declare what it records, and switch to it. Under `_lock`.

        The store's work comes before the switch, off the rig's lock; the session it
        replaces is retired, for the caller to close once the lock is let go.
        """
        rig = self.rig
        start_ns = fields.pop("start_ns", None)
        own = self.provenance()
        fields["flyball_version"] = own["flyball_version"]
        fields["packages"] = {**(fields.get("packages") or {}), **own["packages"]}
        fields["hardware"] = _merged(own["hardware"], fields.get("hardware"))
        writer = self.store.open_session(
            rig.clock.now_ns() if start_ns is None else start_ns, **fields
        )
        chosen = signals is not None

        def failed(error: Exception) -> None:
            self._failed(session, error)

        session = SessionRecorder(
            writer,
            self._recorded() if signals is None else signals,
            [c for _, c in rig.controllers.items()] if controllers is None else controllers,
            on_failure=failed,
        )
        self._switch(session)
        if not chosen:  # a device added while it opened, before the switch
            session.declare(self._recorded())
        rig.conditions.clear(rig, Code.RECORDING_FAILED, message="recording again")
        return session

    def _recorded(self) -> list[Signal]:
        """What a session records by default: each signal that publishes or is written."""
        return [
            s
            for device in list(self.rig.devices.values())
            for s in device.signals.values()
            if (Access.P in s.access or Access.W in s.access) and s.spec.record
        ]

    def _failed(self, session: SessionRecorder, error: Exception) -> None:
        """From the session recorder's thread: switch away from it, so the edge is not sent to it.

        A `recording_failed` condition on the rig until the next session starts.
        """
        with self._lock:
            if self._session is session:
                self._switch(None, was=session)
            with suppress(ValueError):
                self._retired.remove(session)  # ended here, not closed: its thread is this one
        rig = self.rig
        rig.conditions.set(
            rig,
            Code.RECORDING_FAILED,
            Severity.ERROR,
            f"recording stopped: {type(error).__name__}: {error}",
        )
        with suppress(Exception):  # the store already failed once
            session.writer.end(rig.clock.now_ns())

    # endregion

    # region The runner's housekeeping

    def start(self, settings: RunnerConfig | None = None, period_s: float = 30.0) -> None:
        """Keep the scratch record and sweep the store, as `settings` say; None does neither.

        Sweeps once at once (which opens the scratch record), then every `period_s` of wall
        time on a thread of its own until `stop`.
        """
        if settings is None:
            return
        self._keep_scratch = bool(settings.keep_ns)
        self._retention = Retention(self, settings, period_s)
        self._retention.start()

    def sweep(self) -> None:
        """One sweep now, as `start`'s thread makes them; nothing before `start`."""
        if (retention := self._retention) is not None:
            retention.sweep()

    def stop(self) -> None:
        """Stop sweeping and keeping scratch, and end whatever is being recorded.

        A session can still be started after: it is recorded until ended, with no scratch
        record after it.
        """
        self._keep_scratch = False
        retention, self._retention = self._retention, None
        if retention is not None:
            retention.stop()
        with self._lock:
            if self._session is not None:
                self._switch(None)
        self._close_retired()

    # endregion


# endregion
