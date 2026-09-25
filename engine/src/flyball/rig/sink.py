"""What the rig hands its history to: one ordered stream of immutable rows (D-095, D-096).

The rig does not record. It makes a row of each thing worth keeping -- a
delivery's published samples, its controllers' ticks and its write states
(under its lock); an event (under its lock or off it); the signals of a device
added -- at the moment it happens, and numbers it and puts it to the one
[RecordSink][flyball.rig.sink.RecordSink] attached, if any, in one step: the sink
sees every row in the order of its number. It knows nothing of
sessions or stores: the recorder (`flyball.runtime.recorder.Recorder`) is the
sink, and a [Marker][flyball.rig.sink.Marker] it puts through
[Rig.mark][flyball.rig.rig.Rig.mark] -- to open, switch or end a session --
travels in the same stream, so the rows before it and the rows after it land on
either side of the switch whoever reads the stream and whenever.

Today the stream is a synchronous call on the thread that made the row; the
hub (D-095) will replace it with a queue and a consumer thread. The rows are
immutable and complete, so nothing changes for the recorder when that happens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from flyball.foundation.device import Event, Reading, Sample, Signal, WriteState
    from flyball.model.controller import Controller


@dataclass(frozen=True, slots=True)
class TickRow:
    """A controller's state right after one tick, taken then: nothing later changes it."""

    controller: str
    time_ns: int
    """The reading's time; a re-apply's own time when there was no reading."""
    mode: str
    correction: float | None
    measured_value: float | None
    setpoint: float | None
    output_value: float | None
    expected: float | None
    delivered_correction: float | None
    reapplied: bool
    """No reading: the output re-applied on the controller's own period (E25)."""


def tick_row(controller: Controller, reading: Reading | None, time_ns: int) -> TickRow:
    """`controller` as it is now, having ticked on `reading` (None: a re-apply at `time_ns`)."""
    at = time_ns if reading is None else reading.time_ns
    return TickRow(
        controller=controller.name,
        time_ns=at,
        mode=controller.mode.value,
        correction=controller.correction,
        measured_value=reading.value if reading is not None and reading.usable else None,
        setpoint=None if controller.reference is None else controller.setpoint_at(at),
        output_value=controller.output_value,
        expected=controller.expected,
        delivered_correction=controller.delivered_correction,
        reapplied=reading is None,
    )


@dataclass(frozen=True, slots=True)
class Delivered:
    """One delivery's rows, or a commit's made outside one (a manual demand, a command)."""

    seq: int
    time_ns: int
    """The commit's time: what the write states are stamped with."""
    samples: tuple[Sample, ...]
    ticks: tuple[TickRow, ...]
    states: tuple[tuple[Signal, WriteState], ...]


@dataclass(frozen=True, slots=True)
class Published:
    """An event the rig published."""

    seq: int
    event: Event


@dataclass(frozen=True, slots=True)
class Declared:
    """A device added: its recorded signals, from here on."""

    seq: int
    signals: tuple[Signal, ...]


@dataclass(frozen=True, slots=True)
class Marker:
    """The sink's own message, put in the stream by [Rig.mark][flyball.rig.rig.Rig.mark].

    The rig does not read `message`; it only orders it among the rows.
    """

    seq: int
    message: object


type Row = Delivered | Published | Declared | Marker


class RecordSink(Protocol):
    """Takes the rig's rows, in `seq` order.

    `put` is called under the rig's small numbering lock, often under its main lock too:
    it must not block (buffer, never write) and must not call back into the rig.
    """

    def put(self, row: Row) -> None: ...
