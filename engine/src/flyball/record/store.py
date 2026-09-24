"""The store's two faces.

A [SessionWriter][flyball.record.store.SessionWriter] is bound to one open session
and only appends; a [Store][flyball.record.store.Store] opens sessions and reads
any of them. The rig holds a writer on its thread, the server the store on
another, and either can be replaced without the other noticing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from flyball.foundation.device import Device, Sample, Signal, WriteState

from .types import (
    ControllerRow,
    DashboardRow,
    DeviceRow,
    Downsample,
    Event,
    LiveValueRow,
    ProgramFormat,
    ProgramRow,
    RigVersionRow,
    SampleRow,
    Series,
    SessionKind,
    SessionRow,
    SignalRow,
    Span,
    SpanKind,
    Tick,
    TuningRow,
    Window,
    WriteRow,
    WriteStateRow,
)

if TYPE_CHECKING:
    from flyball.model.controller import Controller


class SessionWriter(Protocol):
    """Appends to one session. Declare before you write; end when you are done."""

    @property
    def session(self) -> SessionRow: ...

    # region Declarations

    def declare_device(self, device: Device) -> None:
        """Register a device: its name, driver, config and label. Idempotent."""
        ...

    def declare_signal(self, signal: Signal) -> None:
        """Register a signal with its metadata; a writable one also as a `write`. Idempotent.

        Its device must already be declared.
        """
        ...

    def declare_controller(self, controller: Controller) -> None:
        """`controller.output_signal` and `.measured_signal` must already be declared."""
        ...

    # endregion

    # region Data

    def write_samples(self, samples: Iterable[Sample]) -> None:
        """One delivery's worth, in one transaction; `seq` is assigned here, per device.

        Every signal a sample carries must be declared.
        """
        ...

    def write_states(self, offset_ns: int, states: Mapping[Signal, WriteState]) -> None:
        """What a commit set each signal to, at `offset_ns`. Signals must be declared writable."""
        ...

    def write_tick(self, tick: Tick) -> None: ...

    def write_ticks(self, ticks: Iterable[Tick]) -> None:
        """Many ticks in one transaction. Controllers must be declared."""
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
        rig_version_id: int | None = None,
        kind: SessionKind = "session",
        continues: int | None = None,
    ) -> SessionWriter: ...

    def sessions(
        self, limit: int | None = None, kind: SessionKind | None = None
    ) -> list[SessionRow]:
        """Newest first; every kind unless one is asked for."""
        ...

    def session(self, session_id: int) -> SessionRow: ...

    def end_session(self, session_id: int, end_ns: int | None = None) -> SessionRow:
        """Close a session nothing is writing to any more -- one a crashed runner left open.

        ``end_ns`` defaults to the time of the last sample it holds, or its
        start if it holds none. A session that is already ended raises
        [SessionEndedError][flyball.record.errors.SessionEndedError].
        """
        ...

    def delete_session(self, session_id: int) -> None:
        """Everything the session owns goes with it.

        Need not be atomic: a store may delete a large session a piece at a
        time, so as not to shut everyone else out meanwhile, and marks it
        `details.deleting` before it starts. A reader between the pieces sees
        it partly gone; one cut off part-way is in `deleting_sessions()`, and
        deleting it again finishes it.
        """
        ...

    def deleting_sessions(self) -> list[SessionRow]:
        """Sessions whose `delete_session` never finished: delete each again."""
        ...

    def set_pinned(self, session_id: int, pinned: bool) -> SessionRow:
        """A pinned session is never aged out by retention."""
        ...

    def set_session_name(self, session_id: int, name: str | None) -> SessionRow:
        """Set or clear `details.name` (`sessionName`'s display name).

        Keeps every other `details` key untouched -- a name is one field of the
        free-form document, not the whole of it.
        """
        ...

    def trim_session(self, session_id: int, before_ns: int) -> SessionRow:
        """Drop everything the session recorded before `before_ns` (absolute, in the rig's clock).

        Its `start_ns` moves up to `before_ns` -- the oldest it can now hold --
        but never past its end. A span still open, or ending later, stays.
        How the runner keeps a scratch session to the last `keep`. Need not be
        atomic, as `delete_session`; one cut off part-way leaves `start_ns`
        where it was, and the next trim finishes it.
        """
        ...

    def keep_range(
        self,
        session_id: int,
        start_ns: int,
        end_ns: int,
        details: Any = None,
        kind: SessionKind = "session",
    ) -> SessionRow:
        """Copy `[start_ns, end_ns)` (absolute) of a session into a new closed one, and return it.

        The declarations come over whole; readings, write states, ticks and
        events within the range come rebased to the new start. Spans do not.
        `version`, `config`, `hardware` and the rig version are the source's;
        `details` is the new session's own. Raises `ValueError` when the range
        is not within what the source holds: before its `start_ns`, after its
        end, or empty.
        """
        ...

    def backfill(self, session_id: int, source_id: int, start_ns: int, end_ns: int) -> int:
        """Copy `[start_ns, end_ns)` of `source_id` into an open session someone is writing.

        The range is clamped to what the source holds, its last instant
        included. Rows are matched by address to what the target has
        declared; a signal it has not is left out. Backfilled samples count
        down from zero, below the writer's own sequence. Returns the number
        of readings copied.
        """
        ...

    def measure_session(self, session_id: int) -> int:
        """An estimate of what the session takes on disk, in bytes; kept on the row as `bytes`."""
        ...

    def used_bytes(self) -> int:
        """What the store's file holds, less free pages: what a deletion actually gives back."""
        ...

    # endregion

    # region What a session recorded

    def devices(self, session_id: int) -> list[DeviceRow]: ...

    def signals(self, session_id: int) -> list[SignalRow]:
        """Every signal declared, by device then address."""
        ...

    def writes(self, session_id: int) -> list[WriteRow]: ...

    def controllers(self, session_id: int) -> list[ControllerRow]: ...

    def series(
        self,
        session_id: int,
        address: str,
        window: Window | None = None,
        downsample: Downsample | None = None,
    ) -> Series:
        """One signal over a window.

        `downsample` is a [Downsample][flyball.record.types.Downsample], or `None`
        for every reading. Only a float signal can be downsampled; asking for
        one on any other dtype raises `ValueError`.
        """
        ...

    def ticks(
        self,
        session_id: int,
        controller: str,
        window: Window | None = None,
        every: int | None = None,
    ) -> list[Tick]:
        """A controller's ticks in order; `every` keeps one in `every`, for a plot of a long run."""
        ...

    def samples(
        self, session_id: int, address: str, window: Window | None = None
    ) -> list[SampleRow]:
        """Every sample on a device, or on one node of it, in order, values by signal address."""
        ...

    def write_states(
        self, session_id: int, address: str, window: Window | None = None
    ) -> list[WriteStateRow]:
        """What one writable signal was set to, in order."""
        ...

    def events(
        self, session_id: int, window: Window | None = None, code: str | None = None
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
        controller: str | None = None,
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

        Raises [ProgramNotFoundError][flyball.record.errors.ProgramNotFoundError] when
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

    # region Live values

    def live_values(self) -> list[LiveValueRow]:
        """Every live value kept across restarts, by device then signal."""
        ...

    def put_live_value(self, row: LiveValueRow) -> None:
        """Keep `row`, replacing what was kept for its `(device, signal)`.

        Raises:
            ValueError: Its value is a secret (a `SecretStr`, `SecretBytes`): never kept.
        """
        ...

    def delete_live_value(self, device: str, signal: str) -> None:
        """Forget what was kept for `(device, signal)`; nothing if there was none."""
        ...

    # endregion

    # region Rig versions

    def save_rig_version(
        self, time_ns: int, reason: str, document: dict[str, Any], files: Sequence[str] = ()
    ) -> RigVersionRow:
        """Record the rig as it now stands, and why: the whole document, never a diff.

        The new version's parent is the head, and it becomes the head.
        """
        ...

    def rig_versions(self, limit: int | None = None) -> list[RigVersionRow]:
        """Newest first."""
        ...

    def rig_version(self, version_id: int) -> RigVersionRow: ...

    def head_rig_version(self) -> RigVersionRow | None:
        """The version the rig is at: the last saved, or the last restored."""
        ...

    def set_rig_head(self, version_id: int) -> RigVersionRow:
        """Make `version_id` the head, as a restore does; nothing is copied."""
        ...

    # endregion

    def close(self) -> None: ...
