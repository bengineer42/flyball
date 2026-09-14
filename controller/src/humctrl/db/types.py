"""What comes out of a store, and what goes in that has no core type.

Rows are plain frozen values: no store knowledge, no live objects, so a server
can serialise them straight out and a plot can consume them without the rig.
Times inside a session are ``offset_ns`` from the session's ``start_ns``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from humctrl.core.utils import Labelled

# region Declarations


@dataclass(frozen=True, slots=True)
class SessionRow:
    id: int
    start_ns: int
    end_ns: int | None
    version: str | None
    config: Any
    hardware: Any
    details: Any

    @property
    def open(self) -> bool:
        return self.end_ns is None

    @property
    def duration_ns(self) -> int | None:
        return None if self.end_ns is None else self.end_ns - self.start_ns


@dataclass(frozen=True, slots=True)
class QuantityRow:
    id: int
    name: str
    unit: str
    label: str | None


@dataclass(frozen=True, slots=True)
class SourceRow:
    id: int
    name: str
    kind: str | None


@dataclass(frozen=True, slots=True)
class ChannelRow:
    source: SourceRow
    quantity: QuantityRow

    @property
    def name(self) -> str:
        return f"{self.source.name}.{self.quantity.name}"


@dataclass(frozen=True, slots=True)
class LoopRow:
    name: str
    channel: ChannelRow
    actuator: str
    config: Any


@dataclass(frozen=True, slots=True)
class TuningRow:
    """A named law and its gains. ``config`` is the law's constructor arguments."""

    id: int
    name: str
    law: str
    config: dict[str, Any]
    created_ns: int
    session_id: int | None = None
    loop: str | None = None
    notes: Any = None


# endregion

# region Data


@dataclass(frozen=True, slots=True)
class Point:
    offset_ns: int
    value: float


@dataclass(frozen=True, slots=True)
class Downsample:
    """How to thin a series. Exactly one of the three.

    ``every`` keeps every nth sample by sequence number: cheap, every point a
    real reading, but periodic noise can alias and a spike between kept
    samples vanishes. ``bucket_ns`` averages each bucket of that size;
    ``max_points`` averages into buckets sized so the window fits in that many
    points -- the store resolves it to a ``bucket_ns`` and reports that back.
    """

    every: int | None = None
    bucket_ns: int | None = None
    max_points: int | None = None

    def __post_init__(self) -> None:
        if sum(v is not None for v in (self.every, self.bucket_ns, self.max_points)) != 1:
            raise ValueError("Downsample takes exactly one of every, bucket_ns, max_points")


@dataclass(frozen=True, slots=True)
class Series:
    """One channel over a window, ready to plot.

    ``downsample`` is what was applied, with ``max_points`` resolved to the
    ``bucket_ns`` actually used, so a client can label the axis.
    """

    channel: ChannelRow
    points: tuple[Point, ...]
    downsample: Downsample | None = None

    @property
    def unit(self) -> str:
        return self.channel.quantity.unit

    def __len__(self) -> int:
        return len(self.points)


@dataclass(frozen=True, slots=True)
class Tick:
    """One loop step. Written by the loop's recorder, read back for control plots."""

    loop: str
    offset_ns: int
    mode: str
    correction: float
    reading: float | None = None
    setpoint: float | None = None
    demand: float | None = None
    expected: float | None = None
    delivered_correction: float | None = None


@dataclass(frozen=True, slots=True)
class Event:
    """Something non-numeric that happened: a fault, a retune, a flag."""

    offset_ns: int
    kind: str
    source: str | None = None
    detail: Any = None
    id: int | None = None


class SpanKind(Labelled):
    PROGRAM = "program", "A whole program"
    RUN = "run", "One run of a program"
    COMMAND = "command", "One step"
    NOTE = "note", "An operator note"


@dataclass(frozen=True, slots=True)
class Span:
    """A labelled interval on the timeline; ``end_ns`` is None while open."""

    id: int
    kind: SpanKind
    label: str
    start_ns: int
    end_ns: int | None = None
    parent_id: int | None = None
    details: Any = None


@dataclass(frozen=True, slots=True)
class Window:
    """A half-open range of offsets, ``[start_ns, end_ns)``. None means unbounded."""

    start_ns: int | None = None
    end_ns: int | None = None

    def contains(self, offset_ns: int) -> bool:
        return (self.start_ns is None or offset_ns >= self.start_ns) and (
            self.end_ns is None or offset_ns < self.end_ns
        )


# endregion


def _enum_value(value: Enum | str) -> str:
    return value.value if isinstance(value, Enum) else value
