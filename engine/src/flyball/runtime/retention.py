"""The runner's housekeeping over the store: rotation, retention, the cap, the scratch record.

The recorder ([Recorder][flyball.runtime.recorder.Recorder]) records a rig into
at most one session at a time. While nobody has started one, the runner has it
record into a *scratch* session of its own
([SessionKind][flyball.record.types.SessionKind] `"scratch"`), so a chart has
the last `keep` of history on a rig nobody is recording, and any part of
it can be kept as a session proper (`Store.keep_range`) or folded into the
recording that follows (`Store.backfill`). A recording someone starts
replaces the scratch session; when it ends, a fresh scratch session opens.
The recorder does both; this module sweeps.

A sweep runs every `period_s` of wall time and, in the rig's clock:

- **rotates** a recording older than `rotate` into a continuation;
- **retains**: deletes unpinned recordings that ended more than `retain` ago;
- **trims** every scratch session to the last `keep`, and under `keep_size`;
- **fits** the store under `max_store`, deleting the oldest data first
  whatever its kind -- a scratch session's oldest rows or a whole unpinned
  recording, whichever is older -- never a pinned one, never the recording
  in progress;
- **reopens** the scratch session if nothing is being recorded.

`0` for a duration or size switches that part off. Windows are in the rig's
clock, so a simulated hour is an hour of the simulation; sizes are bytes
on disk. A SQLite file does not shrink when rows go: the pages are reused,
which is what `max_store` measures against.
"""

from __future__ import annotations

import logging
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING

from flyball.record import SessionRow

if TYPE_CHECKING:
    from flyball.runtime.config import RunnerConfig
    from flyball.runtime.recorder import Recorder

log = logging.getLogger("flyball.retention")

FIT_STEPS = 50
"""How many deletions one sweep makes towards `max_store` before leaving the rest to the next."""


class Retention:
    """Runs the sweeps on a thread, for the recorder that owns it (`Recorder.start`).

    Args:
        recorder: Whose sessions are swept around: its rig's clock sets the windows, its
            recording is never retained, and it rotates and reopens the scratch record.
        settings: The `runner:` section: `keep`, `keep_size`, `retain`, `rotate`, `max_store`.
        period_s: Wall seconds between sweeps.
    """

    def __init__(self, recorder: Recorder, settings: RunnerConfig, period_s: float = 30.0) -> None:
        self.recorder = recorder
        self.store = recorder.store
        self.keep_ns = settings.keep_ns
        self.keep_bytes = settings.keep_bytes
        self.retain_ns = settings.retain_ns
        self.rotate_ns = settings.rotate_ns
        self.max_bytes = settings.max_bytes
        self.period_s = period_s
        self._lock = Lock()  # one sweep at a time
        self._stop = Event()
        self._thread = Thread(target=self._run, daemon=True, name="retention")

    def start(self) -> None:
        """Sweep once, which opens the scratch record, and keep sweeping until `stop`."""
        self.sweep()
        self._thread.start()

    def stop(self) -> None:
        """Stop sweeping. The scratch session stays open for the recorder to close."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join()

    def sweep(self) -> None:
        """One pass: rotate, retain, trim, fit, reopen. Safe from any thread."""
        with self._lock:
            now = self.recorder.rig.clock.now_ns()
            self._rotate()
            self._retain(now)
            self._trim(now)
            self._fit(now)
            self.recorder.ensure_scratch()

    def _run(self) -> None:
        while not self._stop.wait(self.period_s):
            try:
                self.sweep()
            except Exception:
                log.exception("sweep failed; trying again in %gs", self.period_s)

    # region The steps

    def _rotate(self) -> None:
        if not self.rotate_ns:
            return
        previous = self.recorder.recording
        continued = self.recorder.rotate(self.rotate_ns)
        if previous is not None and continued is not None:
            log.info(
                "rotated session %d into %d",
                previous.writer.session.id,
                continued.writer.session.id,
            )

    def _retain(self, now: int) -> None:
        if not self.retain_ns:
            return
        cutoff = now - self.retain_ns
        for session in self.store.sessions(kind="session"):
            if session.end_ns is not None and not session.pinned and session.end_ns < cutoff:
                self.store.delete_session(session.id)
                log.info("session %d retained %s: deleted", session.id, _ago(now, session.end_ns))

    def _trim(self, now: int) -> None:
        for row in self.store.sessions(kind="scratch"):
            session: SessionRow | None = row
            if self.keep_ns:
                session = self._trim_to(row, now - self.keep_ns, now)
                if session is None:
                    continue
            elif row.end_ns is not None:
                self.store.delete_session(row.id)  # nothing is kept: an old run's scratch
                continue
            if not self.keep_bytes:
                continue
            size = self.store.measure_session(session.id)
            if size <= self.keep_bytes:
                continue
            held = (now if session.end_ns is None else session.end_ns) - session.start_ns
            # Cut a little more than the overshoot: the estimate is coarse and the next
            # sweep would otherwise cut again for a few bytes.
            cut = session.start_ns + round(held * (1 - self.keep_bytes / size) * 1.05)
            if self._trim_to(session, cut, now) is not None:
                self.store.measure_session(session.id)

    def _trim_to(self, session: SessionRow, before_ns: int, now: int) -> SessionRow | None:
        """Trim `session` to what is at or after `before_ns`; None if that left it empty."""
        if session.end_ns is not None and session.end_ns <= before_ns:
            self.store.delete_session(session.id)
            log.info("scratch session %d aged out: deleted", session.id)
            return None
        if before_ns <= session.start_ns:
            return session
        return self.store.trim_session(session.id, before_ns)

    def _fit(self, now: int) -> None:
        if not self.max_bytes:
            return
        recording = self.recorder.recording
        live = None if recording is None else recording.writer.session.id
        for _ in range(FIT_STEPS):
            used = self.store.used_bytes()
            if used <= self.max_bytes:
                return
            oldest = min(
                (
                    s
                    for s in self.store.sessions()
                    if not s.pinned and s.id != live and (s.scratch or s.end_ns is not None)
                ),
                key=lambda s: s.start_ns,
                default=None,
            )
            if oldest is None:
                log.warning(
                    "store is %d bytes over max_store and nothing more can go:"
                    " what is left is pinned or being recorded",
                    used - self.max_bytes,
                )
                return
            if oldest.scratch:
                held = (now if oldest.end_ns is None else oldest.end_ns) - oldest.start_ns
                self._trim_to(oldest, oldest.start_ns + max(held // 10, 1), now)
            else:
                self.store.delete_session(oldest.id)
                log.info("store over max_store: deleted session %d (the oldest)", oldest.id)
        log.info("store still over max_store after %d deletions; more next sweep", FIT_STEPS)

    # endregion


def _ago(now: int, then: int) -> str:
    seconds = (now - then) / 1e9
    if seconds >= 86400:
        return f"{seconds / 86400:.1f} d ago"
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} h ago"
    return f"{seconds / 60:.0f} min ago"
