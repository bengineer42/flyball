"""What comes out of a store, and what goes in that has no core type.

Rows are plain frozen values with no live objects, so a server serialises
them directly. Times inside a session are `offset_ns` from its `start_ns`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from flyball.foundation.device import Band, Limit
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
    range: Band | None = None
    precision: int | None = None
    warn: Band | None = None
    alarm: Band | None = None
    limits: Band | None = None

    @property
    def device(self) -> str:
        """The device's name: the first segment of the address."""
        return self.address.partition(".")[0]


@dataclass(frozen=True, slots=True)
class WriteRow:
    """A writable signal whose writes the session recorded."""

    signal: SignalRow
    driver: str | None
    limits: Band | None

    @property
    def address(self) -> str:
        return self.signal.address


@dataclass(frozen=True, slots=True)
class ControllerRow:
    """A controller is named by the signal it drives; `source` is the one it regulates."""

    name: str
    source: str
    law: Any
    feedforward: Any = None
    """The feedforward's config; None in sessions recorded before there was one."""


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


@dataclass(frozen=True, slots=True)
class PasskeyRow:
    """A registered passkey. `public_key` is COSE bytes -- fine to hold, never to send."""

    id: int
    credential_id: bytes
    public_key: bytes
    sign_count: int
    aaguid: bytes | None
    transports: list[str]
    label: str
    created_ns: int


# endregion

# region Data


@dataclass(frozen=True, slots=True)
class Point:
    """One reading. `value` is a float for a float signal, else whatever its dtype decodes to."""

    offset_ns: int
    value: Any


@dataclass(frozen=True, slots=True)
class SampleRow:
    """One stored sample: the values under `node` at one instant, by signal address."""

    seq: int
    offset_ns: int
    node: str
    values: dict[str, Any]


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
    reading: float | None = None
    setpoint: float | None = None
    demand: float | None = None
    expected: float | None = None
    delivered_correction: float | None = None


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
