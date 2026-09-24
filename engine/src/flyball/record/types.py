"""What comes out of a store, and what goes in that has no core type.

Rows are plain frozen values with no live objects, so a server serialises
them directly. Times inside a session are `offset_ns` from its `start_ns`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Literal

from flyball.foundation.device import Bounds, Limit, NoValue, Quality, Reason
from flyball.foundation.primitives import Labelled

# region Declarations


SessionKind = Literal["session", "scratch"]
"""`session`: a recording someone started. `scratch`: the rolling record the runner keeps
while nothing is being recorded, trimmed to the last `keep` of the rig's clock."""


@dataclass(frozen=True, slots=True)
class SessionRow:
    id: int
    start_ns: int
    """When the session started -- or, for a scratch session, the oldest row it still holds:
    trimming moves it forward. Offsets are from here either way."""
    end_ns: int | None
    version: str | None
    config: Any
    hardware: Any
    details: Any
    rig_version_id: int | None = None
    """The rig version the session started on, when the store keeps rig versions."""
    kind: SessionKind = "session"
    pinned: bool = False
    """Never aged out by retention."""
    continues: int | None = None
    """The session this one carried on from when the runner rotated at a boundary."""
    bytes: int | None = None
    """What a scratch session holds on disk, as last estimated; None where not measured."""

    @property
    def open(self) -> bool:
        return self.end_ns is None

    @property
    def scratch(self) -> bool:
        return self.kind == "scratch"

    @property
    def duration_ns(self) -> int | None:
        return None if self.end_ns is None else self.end_ns - self.start_ns


@dataclass(frozen=True, slots=True)
class DeviceRow:
    """A device as declared for the session: its name, and what built it."""

    id: int
    address: str
    driver: str | None
    config: Any
    label: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class SignalRow:
    """One signal as declared for the session; `address` is the key everything else uses."""

    id: int
    device_id: int
    address: str
    quantity: str
    unit: str
    access: str
    """The wire form: `"rp"`, `"w"`, `"rpw"`."""
    dtype: str = "float"
    shape: list[int] = field(default_factory=list)
    label: str | None = None
    range: Bounds | None = None
    precision: int | None = None
    warning: Bounds | None = None
    alarm: Bounds | None = None
    limits: Bounds | None = None

    @property
    def device(self) -> str:
        """The device's name: the first segment of the address."""
        return self.address.partition(".")[0]


@dataclass(frozen=True, slots=True)
class WriteRow:
    """A writable signal whose writes the session recorded."""

    signal: SignalRow
    driver: str | None
    limits: Bounds | None

    @property
    def address(self) -> str:
        return self.signal.address


@dataclass(frozen=True, slots=True)
class ControllerRow:
    """A controller is named by the demand it drives; `measured` is the signal it regulates."""

    name: str
    measured: str
    law: Any
    feedforward: Any = None
    """The feedforward's config; None in sessions recorded before there was one."""


@dataclass(frozen=True, slots=True)
class LatchRow:
    """A latch kept across restarts: a stop's or a fault action's, until a person resets it.

    Keyed by `cause` (`stop`, `on_fault:<controller>`). `subjects` is `[{subject_kind, subject}]`.
    """

    cause: str
    subjects: list[dict[str, str]]
    by: str
    at_ns: int
    """When it was set, wall time in ns since the epoch."""
    reason: str = ""
    action: str = ""


@dataclass(frozen=True, slots=True)
class LiveValueRow:
    """A live value kept across restarts: a `driver: values` entry's last write (C10(5)).

    Keyed by `(device, signal)`. `kind` is `value` (a values device's) or `setting` (a driver
    setting behind a config field, C11). `value` and `initial` are what JSON holds.
    """

    device: str
    signal: str
    """The path under the device: `dry_supply`."""
    kind: Literal["value", "setting"]
    value: Any
    unit: str | None
    """The unit symbol when it was written; None: unitless."""
    initial: Any
    """The rig file's value in force when it was written."""
    writer: str | None
    """Who wrote it: the principal's `sub`; None when not known."""
    written_ns: int
    """When, wall time in ns since the epoch."""
    config_field: str | None = None
    """A setting's driver config field; None for a value."""
    head_version: int | None = None
    """The rig version in force when it was written."""


@dataclass(frozen=True, slots=True)
class RigVersionRow:
    """The rig file as it stood at one moment, and why it changed."""

    id: int
    time_ns: int
    reason: str
    """`loaded`, `added device blender`, `removed link pwm0`, `restored 3`, ..."""
    files: list[str]
    """The files the runner loaded, for provenance; empty for a rig started bare."""
    document: dict[str, Any]
    """The whole rig document, `RigConfig`'s canonical form: self-contained, never a diff."""
    parent: int | None = None
    """The version this one was made from -- the head when it was saved; None for a first."""


@dataclass(frozen=True, slots=True)
class TuningRow:
    """A named law and its gains. `config` is the law's constructor arguments."""

    id: int
    name: str
    law: str
    config: dict[str, Any]
    created_ns: int
    session_id: int | None = None
    controller: str | None = None
    """The controller it was made for or on, if any."""
    notes: Any = None


ProgramFormat = Literal["yaml", "toml", "json"]


@dataclass(frozen=True, slots=True)
class ProgramRow:
    """A stored program: the document as written, in the format it was written in."""

    id: int
    name: str
    format: ProgramFormat
    body: str
    created_ns: int
    sha256: str
    label: str | None = None
    notes: Any = None


@dataclass(frozen=True, slots=True)
class DashboardRow:
    """A stored dashboard: the document as the UI saved it, for one rig."""

    id: int
    name: str
    rig: str
    body: Any
    created_ns: int
    sha256: str


# endregion

# region Data


class Flag(IntEnum):
    """A stored reading's `flag`: what a row with no value was, or the mark on one with a value.

    Codes 1-15 go with a NULL value (the no-value's quality), 16-31 with a value (a mark);
    NULL is a plain value. Every stale reason but `device_offline` shares `STALE`: which it
    was is in the device's condition edges, or not kept (`silent`, `never_read`, `last_read`).
    """

    INVALID = 1
    NOT_APPLICABLE = 2
    STALE = 3
    STALE_DEVICE_OFFLINE = 4
    AT_LIMIT_LOW = 16
    AT_LIMIT_HIGH = 17

    @classmethod
    def of(cls, value: NoValue) -> Flag:
        """The code a no-value is stored with."""
        if value.quality is Quality.INVALID:
            return cls.INVALID
        if value.quality is Quality.NOT_APPLICABLE:
            return cls.NOT_APPLICABLE
        if value.reason == Reason.DEVICE_OFFLINE:
            return cls.STALE_DEVICE_OFFLINE
        return cls.STALE

    @classmethod
    def mark(cls, at_limit: Limit | None) -> Flag | None:
        """The code a value at a limit is stored with; None with no mark."""
        if at_limit is None:
            return None
        return cls.AT_LIMIT_HIGH if at_limit is Limit.HIGH else cls.AT_LIMIT_LOW

    @property
    def quality(self) -> Quality:
        """The quality it records: a no-value's, or `ok` for a mark."""
        return _FLAG_QUALITY.get(self, Quality.OK)


_FLAG_QUALITY = {
    Flag.INVALID: Quality.INVALID,
    Flag.NOT_APPLICABLE: Quality.NOT_APPLICABLE,
    Flag.STALE: Quality.STALE,
    Flag.STALE_DEVICE_OFFLINE: Quality.STALE,
}


@dataclass(frozen=True, slots=True)
class Point:
    """One reading. `value` is a float for a float signal, else whatever its dtype decodes to.

    `value` is None for a reading with no value, and `flag` says what it was
    ([Flag][flyball.record.types.Flag]: 1 invalid, 2 not_applicable, 3 stale, 4 stale because
    the device was offline); on a value, `flag` is its mark (16 at_limit low, 17 high) or None.
    A bucket of an averaged series is None, with the first no-value code in it, when any
    reading in it had no value.
    """

    offset_ns: int
    value: Any
    flag: int | None = None


@dataclass(frozen=True, slots=True)
class SampleRow:
    """One stored sample: the values under `node` at one instant, by signal address.

    A value is None for a reading with none; `flags` holds the stored `flag` of the values
    that have one (a no-value's code, or a mark), by address.
    """

    seq: int
    offset_ns: int
    node: str
    values: dict[str, Any]
    flags: dict[str, int] = field(default_factory=dict[str, int])


@dataclass(frozen=True, slots=True)
class Downsample:
    """How to thin a series. Exactly one of the three.

    `every` keeps every nth sample: cheap, but can alias and miss spikes.
    `bucket_ns` averages each bucket. `max_points` averages into buckets sized
    to fit the window; the store reports the resolved `bucket_ns`.
    """

    every: int | None = None
    bucket_ns: int | None = None
    max_points: int | None = None

    def __post_init__(self) -> None:
        if sum(v is not None for v in (self.every, self.bucket_ns, self.max_points)) != 1:
            raise ValueError("Downsample takes exactly one of every, bucket_ns, max_points")


@dataclass(frozen=True, slots=True)
class Series:
    """One signal over a window. `downsample` is what was applied, `max_points` resolved."""

    signal: SignalRow
    points: tuple[Point, ...]
    downsample: Downsample | None = None

    @property
    def unit(self) -> str:
        return self.signal.unit

    def __len__(self) -> int:
        return len(self.points)


@dataclass(frozen=True, slots=True)
class Tick:
    """One controller step. Written by the recorder, read back for control plots."""

    controller: str
    offset_ns: int
    mode: str
    correction: float | None
    """None when the law's output was not a number (a NaN integral)."""
    measured: float | None = None
    setpoint: float | None = None
    output: float | None = None
    expected: float | None = None
    delivered_correction: float | None = None
    reapplied: bool = False
    """A re-apply between readings (a moving setpoint's feedforward, E25): no reading, so
    `measured` is None and the law did not step."""


@dataclass(frozen=True, slots=True)
class WriteStateRow:
    """What one writable signal was set to at one instant; a `WriteState` with its time."""

    offset_ns: int
    value: float | None
    requested: float | None = None
    at_limit: Limit | None = None
    controller: str | None = None


@dataclass(frozen=True, slots=True)
class Event:
    """Something non-numeric that happened: a fault, a retune, a flag."""

    offset_ns: int
    code: str
    subject: str | None = None
    """What it is about: a device, a signal, a controller, a program step, the rig."""
    details: Any = None
    id: int | None = None
    edge: str | None = None
    """`raised` or `cleared` for a condition's start or end; None for a point event."""


class SpanKind(Labelled):
    PROGRAM = "program", "A whole program"
    RUN = "run", "One run of a program"
    COMMAND = "command", "One step"
    NOTE = "note", "An operator note"


@dataclass(frozen=True, slots=True)
class Span:
    """A labelled interval on the timeline; `end_ns` is None while open."""

    id: int
    kind: SpanKind
    label: str
    start_ns: int
    end_ns: int | None = None
    parent_id: int | None = None
    details: Any = None


@dataclass(frozen=True, slots=True)
class Window:
    """A half-open range of offsets, `[start_ns, end_ns)`. None means unbounded."""

    start_ns: int | None = None
    end_ns: int | None = None

    def contains(self, offset_ns: int) -> bool:
        return (self.start_ns is None or offset_ns >= self.start_ns) and (
            self.end_ns is None or offset_ns < self.end_ns
        )


# endregion


def _enum_value(value: Enum | str) -> str:
    return value.value if isinstance(value, Enum) else value
