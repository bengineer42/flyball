"""Runs readers on their periods and keeps each one's run state.

What the runtime knows about a reader -- period, last delivery, whether it is
stopped on an error -- lives here, beside the device's own state, and is
pushed through `runs`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from flyball.core.device import Condition, DeviceState, Level
from flyball.core.errors import ConflictError, NotFoundError
from flyball.core.reading import Channel, Reader, Sample
from flyball.core.topic import Latest
from flyball.core.utils import PeriodicLoop

log = logging.getLogger("flyball.readers")

if TYPE_CHECKING:
    from flyball.runtime.rig import Rig


@dataclass(frozen=True, slots=True, kw_only=True)
class ReaderRun:
    """How the runtime is running a reader, beside what the reader reports of itself."""

    period_s: float | None = None
    running: bool = False
    last_read_ns: int | None = None
    conditions: tuple[Condition, ...] = ()
    state: DeviceState


class Readers:
    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self.by_name: dict[str, Reader] = {}
        self.periodic: dict[str, PeriodicLoop] = {}
        self.runs: Latest[str, ReaderRun] = Latest()
        """The newest run of each reader, by name, after every read or failure."""
        self._runs: dict[str, ReaderRun] = {}

    def add(self, reader: Reader) -> None:
        """Attach a reader: what it emits reaches the rig from now on. `start_periodic` adds too.

        Claims the reader's name rig-wide, and each of its sources' names
        too when a source is not simply the reader under another hat (most
        readers have exactly one source, named the same as the reader).
        """
        if self.by_name.get(reader.name) is reader:
            return  # already attached; a repeat call (start_periodic re-adds) is a no-op
        claimed: list[str] = []
        try:
            self.rig.claim(reader.name, "reader", reader)
            claimed.append(reader.name)
            for source in reader.sources:
                if source.name != reader.name:
                    self.rig.claim(source.name, "source", source)
                    claimed.append(source.name)
        except ConflictError:
            for name in claimed:  # leave no half-claimed reader behind
                self.rig.release(name)
            raise
        self.by_name[reader.name] = reader
        self.rig.devices[reader.name] = reader
        self._runs.setdefault(reader.name, ReaderRun(state=reader.state))
        reader.attach(lambda samples: self.delivered(reader, samples))

    def delivered(self, reader: Reader, samples: Sequence[Sample]) -> None:
        """Samples from `reader`, pushed or polled: into the rig, then noted as its latest.

        A failure *downstream* -- a law, an observer, the recorder -- is the
        rig's, not the reader's: it becomes an event and the reader carries
        on, its samples still noted as read.
        """
        try:
            with self.rig.lock:
                self.rig.on_read(samples)
        except Exception as error:
            log.exception("delivering %s's samples", reader.name)
            self.rig.event(
                Level.ERROR,
                "rig",
                reader.name,
                "delivery_failed",
                f"{type(error).__name__}: {error}",
                {"reader": reader.name},
            )
        self._update(reader, last_read_ns=max(s.time_ns for s in samples), conditions=())

    def get(self, name: str) -> Reader:
        try:
            return self.by_name[name]
        except KeyError as e:
            raise NotFoundError(f"Reader {name!r} not found") from e

    def run(self, name: str) -> ReaderRun:
        return self._runs[name]

    def start_periodic(self, reader: Reader, period: float) -> None:
        self.add(reader)
        if (loop := self.periodic.pop(reader.name, None)) is not None:
            loop.stop()
        loop = PeriodicLoop(self._read, period, False, reader, clock=self.rig.clock)
        self.periodic[reader.name] = loop
        self._update(reader, period_s=period, running=True)
        loop.start()

    def restart(self, name: str) -> ReaderRun:
        """Poll an offline reader again on its period; a pushed reader just clears its conditions.

        Raises:
            NotFoundError: No such reader.
        """
        reader = self.get(name)
        period = self._runs[name].period_s
        if period is not None:
            self.start_periodic(reader, period)
        self._update(reader, conditions=())
        self.rig.event(Level.INFO, "reader", name, "restarted", "polling again")
        return self._runs[name]

    def stop_all(self) -> None:
        for name, loop in self.periodic.items():
            loop.stop()
            self._update(self.by_name[name], running=False)

    def _read(self, reader: Reader) -> None:
        """One scheduled poll: emit what it returns -- or note the failure and stop polling.

        Only `read` itself can put the reader offline; delivery failures are
        handled in [delivered][flyball.runtime.reader.Readers.delivered].
        A reader that went offline stays stopped until `restart`.
        """
        started = self.rig.clock.monotonic()  # in the rig's time, as the period is
        try:
            samples = reader.read(self.rig.clock.now_ns())
        except Exception as error:
            offline = Condition(
                "offline", Level.ERROR, f"{type(error).__name__}: {error}", self.rig.clock.now_ns()
            )
            self._update(reader, running=False, conditions=(offline,))
            self.rig.event(Level.ERROR, "reader", reader.name, "offline", offline.message)
            if (loop := self.periodic.get(reader.name)) is not None:
                loop.stop(join=False)  # from inside the loop: it exits after this call
            return
        reader.emit(samples)
        period = self._runs[reader.name].period_s
        took = self.rig.clock.monotonic() - started
        if period is not None and took > period:
            slow = Condition(
                "slow",
                Level.WARNING,
                f"read took {took:.2f} s against a {period} s period",
                self.rig.clock.now_ns(),
            )
            self._update(reader, conditions=(slow,))
            self.rig.event(Level.WARNING, "reader", reader.name, "slow", slow.message)

    def _update(self, reader: Reader, **changes: Any) -> None:
        run = replace(self._runs[reader.name], state=reader.state, **changes)
        self._runs[reader.name] = run
        if self.runs.watched:
            self.runs.set(reader.name, run)

    @property
    def channels(self) -> Sequence[Channel]:
        return [ch for name in self.periodic for ch in self.by_name[name].channels]
