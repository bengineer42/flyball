"""Runs readers on their periods and keeps each one's run state.

A reader is a device; what the *runtime* knows about it -- its period, when
it last delivered, whether it is stopped on an error -- is kept here, beside
the device's own state, and pushed through ``runs`` for anyone watching.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from flyball.core.device import Condition, DeviceState, Level
from flyball.core.errors import ConflictError, NotFoundError
from flyball.core.reading import Channel, Reader
from flyball.core.sink import RESERVED_NAMES
from flyball.core.topic import Latest
from flyball.core.utils import PeriodicLoop

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
        #: The newest run of each reader, by name, after every read or failure.
        self.runs: Latest[str, ReaderRun] = Latest()
        self._runs: dict[str, ReaderRun] = {}

    def add(self, reader: Reader) -> None:
        """Make a reader reachable by name. ``start_periodic`` adds its reader."""
        if reader.name in RESERVED_NAMES:
            raise ConflictError(f"Reader name {reader.name!r} is reserved as a route segment")
        if (existing := self.by_name.get(reader.name)) is not None and existing is not reader:
            raise ConflictError(f"Reader {reader.name!r} is already attached")
        self.by_name[reader.name] = reader
        self._runs.setdefault(reader.name, ReaderRun(state=reader.state))

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
        loop = PeriodicLoop(self._read, period, False, reader)
        self.periodic[reader.name] = loop
        self._update(reader, period_s=period, running=True)
        loop.start()

    def stop_all(self) -> None:
        for name, loop in self.periodic.items():
            loop.stop()
            self._update(self.by_name[name], running=False)

    def _read(self, reader: Reader) -> None:
        """One scheduled read: deliver, then note it -- or note the failure and stop."""
        try:
            self.rig.read(reader)
        except Exception as error:
            offline = Condition(
                "offline", Level.ERROR, f"{type(error).__name__}: {error}", self.rig.clock.now_ns()
            )
            self._update(reader, running=False, conditions=(offline,))
            raise
        self._update(reader, last_read_ns=self.rig.clock.now_ns(), conditions=())

    def _update(self, reader: Reader, **changes: Any) -> None:
        run = replace(self._runs[reader.name], state=reader.state, **changes)
        self._runs[reader.name] = run
        if self.runs.watched:
            self.runs.set(reader.name, run)

    @property
    def channels(self) -> Sequence[Channel]:
        return [ch for name in self.periodic for ch in self.by_name[name].channels]
