"""The store's two faces.

A [SessionWriter][flyball.db.store.SessionWriter] is bound to one open session
and only appends; a [Store][flyball.db.store.Store] opens sessions and reads
any of them. The rig holds a writer on its thread, the server the store on
another, and either can be replaced without the other noticing.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from flyball.core.reading import Channel, Sample, Source

from .types import (
    ActuatorRow,
    ChannelRow,
    DashboardRow,
    Downsample,
    Event,
    LoopRow,
    ProgramFormat,
    ProgramRow,
    SampleRow,
    Series,
    SessionRow,
    SourceRow,
    Span,
    SpanKind,
    Tick,
    TuningRow,
    Window,
)


class SessionWriter(Protocol):
    """Appends to one session. Declare before you write; end when you are done."""

    @property
    def session(self) -> SessionRow: ...

    # region Declarations

    def declare_source(self, source: Source, kind: str | None = None) -> None:
        """Register a source and every measurand it carries. Idempotent."""
        ...

    def declare_actuator(self, name: str, kind: str, config: Any = None) -> None:
        """Idempotent."""
        ...

    def declare_loop(
        self, name: str, channel: Channel, config: Any = None, feedforward: Any = None
    ) -> None:
        """`name` is the actuator's, which must already be declared."""
        ...

    # endregion

    # region Data

    def write_samples(self, samples: Iterable[Sample]) -> None:
        """One delivery's worth, in one transaction. Sources must be declared."""
        ...

    def write_tick(self, tick: Tick) -> None: ...

    def write_ticks(self, ticks: Iterable[Tick]) -> None:
        """Many ticks in one transaction. Loops must be declared."""
        ...

    def write_event(self, event: Event) -> int:
        """Returns the event's id."""
        ...

    def open_span(
        self,
        kind: SpanKind,
        label: str,
        start_ns: int,
        parent_id: int | None = None,
        details: Any = None,
    ) -> int:
        """Returns the span's id, for `close_span` and for children."""
        ...

    def close_span(self, span_id: int, end_ns: int, details: Any = None) -> None: ...

    # endregion

    def end(self, end_ns: int) -> None:
        """Close the session. Every write after this raises."""
        ...


class Store(Protocol):
    """Everything recorded, for reading back; and the way to start recording more."""

    # region Sessions

    def open_session(
        self,
        start_ns: int,
        version: str | None = None,
        config: Any = None,
        hardware: Any = None,
        details: Any = None,
    ) -> SessionWriter: ...

    def sessions(self, limit: int | None = None) -> list[SessionRow]:
        """Newest first."""
        ...

    def session(self, session_id: int) -> SessionRow: ...

    def end_session(self, session_id: int, end_ns: int | None = None) -> SessionRow:
        """Close a session nothing is writing to any more -- one a crashed daemon left open.

        ``end_ns`` defaults to the time of the last sample it holds, or its
        start if it holds none. A session that is already ended raises
        [SessionEndedError][flyball.db.errors.SessionEndedError].
        """
        ...

    def delete_session(self, session_id: int) -> None:
        """Everything the session owns goes with it."""
        ...

    # endregion

    # region What a session recorded

    def sources(self, session_id: int) -> list[SourceRow]: ...

    def channels(self, session_id: int) -> list[ChannelRow]: ...

    def actuators(self, session_id: int) -> list[ActuatorRow]: ...

    def loops(self, session_id: int) -> list[LoopRow]: ...

    def series(
        self,
        session_id: int,
        source: str,
        measurand: str,
        window: Window | None = None,
        downsample: Downsample | None = None,
    ) -> Series:
        """One channel over a window.

        `downsample` is a [Downsample][flyball.db.types.Downsample], or `None`
        for every reading.
        """
        ...

    def ticks(
        self, session_id: int, loop: str, window: Window | None = None, every: int | None = None
    ) -> list[Tick]:
        """A loop's ticks in order; `every` keeps one tick in `every`, for a plot of a long run."""
        ...

    def samples(
        self, session_id: int, source: str, window: Window | None = None
    ) -> list[SampleRow]:
        """Every sample of one source in order, each with all its measurands."""
        ...

    def events(
        self, session_id: int, window: Window | None = None, kind: str | None = None
    ) -> list[Event]: ...

    def spans(self, session_id: int) -> list[Span]:
        """In start order; nest by `parent_id`."""
        ...

    # endregion

    # region Tunings

    def save_tuning(
        self,
        name: str,
        law: str,
        config: dict[str, Any],
        created_ns: int,
        session_id: int | None = None,
        loop: str | None = None,
        notes: Any = None,
    ) -> TuningRow:
        """Add a version under `name`. Earlier versions stay; `tuning` returns the newest."""
        ...

    def tuning(self, name: str) -> TuningRow:
        """The newest version under `name`."""
        ...

    def tunings(self) -> list[TuningRow]:
        """The newest version of every name, by name."""
        ...

    def tuning_history(self, name: str) -> list[TuningRow]:
        """Every version under `name`, newest first."""
        ...

    def delete_tuning(self, name: str) -> None:
        """Every version."""
        ...

    # region Programs

    def save_program(
        self,
        name: str,
        format: ProgramFormat,
        body: str,
        created_ns: int,
        label: str | None = None,
        notes: Any = None,
    ) -> ProgramRow:
        """Add a version under `name`, verbatim. Earlier versions stay; `program` is the newest."""
        ...

    def program(self, name: str) -> ProgramRow: ...

    def program_version(self, program_id: int) -> ProgramRow: ...

    def programs(self) -> list[ProgramRow]:
        """Newest version of every name, by name."""
        ...

    def program_history(self, name: str) -> list[ProgramRow]:
        """Every version under `name`, newest first."""
        ...

    def delete_program(self, name: str) -> None:
        """Every version."""
        ...

    def rename_program(self, name: str, new_name: str) -> list[ProgramRow]:
        """Move every version of `name` under `new_name`; the history comes with it.

        Raises [ProgramNotFoundError][flyball.db.errors.ProgramNotFoundError] when
        there is nothing under `name`, and `ConflictError` when `new_name` is taken.
        """
        ...

    # endregion

    # region Dashboards

    def save_dashboard(self, name: str, rig: str, body: Any, created_ns: int) -> DashboardRow:
        """Add a version under `name`. Earlier versions stay; `dashboard` is the newest."""
        ...

    def dashboard(self, name: str) -> DashboardRow: ...

    def dashboard_version(self, dashboard_id: int) -> DashboardRow: ...

    def dashboards(self, rig: str | None = None) -> list[DashboardRow]:
        """Newest version of every name, by name; for one rig when given."""
        ...

    def dashboard_history(self, name: str) -> list[DashboardRow]: ...

    def delete_dashboard(self, name: str) -> None:
        """Every version."""
        ...

    def rename_dashboard(self, name: str, new_name: str) -> list[DashboardRow]: ...

    # endregion

    # endregion

    def close(self) -> None: ...
