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
    Downsample,
    Event,
    LoopRow,
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

    def declare_loop(self, name: str, channel: Channel, config: Any = None) -> None:
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

    def ticks(self, session_id: int, loop: str, window: Window | None = None) -> list[Tick]: ...

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

    # endregion

    def close(self) -> None: ...
