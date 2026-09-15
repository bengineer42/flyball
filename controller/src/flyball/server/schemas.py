"""Wire format shared across routes.

Separate from the domain types so the HTTP surface and the control code can
change shape independently. Law configs cross as a `tag`-discriminated union
built from the registry. Application-specific requests live with the
application.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, SerializeAsAny, TypeAdapter

from flyball.control import ControlLaws, ControlLawView, Loop, LoopView
from flyball.core.clock import Clock
from flyball.core.model import discriminated_union
from flyball.core.reading import Channel, Sample, Source

LawConfig = discriminated_union(ControlLaws, "tag", lambda law: law.config)
LawsSchema = TypeAdapter(LawConfig).json_schema()


# region Live rig


class ClockOut(BaseModel):
    """The rig's timebase: `now_ns` is wall clock at the request, `elapsed_ns` uptime."""

    start_time_ns: int
    now_ns: int
    elapsed_ns: int
    tags: dict[str, int]
    speed: float = 1.0
    """How fast the rig's time runs against wall time; only a simulated rig is ever not 1."""

    @classmethod
    def of(cls, clock: Clock) -> ClockOut:
        return cls(
            start_time_ns=clock.start_time_ns,
            now_ns=clock.now_ns(),
            speed=float(getattr(clock, "speed", 1.0)),
            elapsed_ns=clock.elapsed_ns(),
            tags={label: clock.elapsed_ns(label) for label in clock.tags_ns if label is not None},
        )


class ChannelOut(BaseModel):
    source: str
    measurand: str
    unit: str
    label: str
    range: tuple[float, float] | None = None
    precision: int | None = None
    warn: tuple[float, float] | None = None
    """The band a value is normal inside; outside it, a warning."""
    alarm: tuple[float, float] | None = None
    """The band a value is acceptable inside; outside it, an alarm."""
    dimension: str | None = None
    """The unit's dimension, so a client can tell what may drive or be compared with what."""

    @classmethod
    def of(cls, channel: Channel) -> ChannelOut:
        return cls(
            source=str(channel.source.name),
            measurand=channel.measurand.name,
            unit=channel.unit.symbol,
            dimension=channel.unit.dimension.label,
            label=channel.measurand.label,
            range=channel.measurand.range,
            precision=channel.measurand.precision,
            warn=channel.measurand.warn,
            alarm=channel.measurand.alarm,
        )


class SampleOut(BaseModel):
    source: str
    seq: int
    time_ns: int
    values: dict[str, float]

    @classmethod
    def of(cls, sample: Sample) -> SampleOut:
        return cls(
            source=sample.source.name,
            seq=sample.seq,
            time_ns=sample.time_ns,
            values={q.name: v for q, v in sample.values.items()},
        )


class SourceOut(BaseModel):
    name: str
    channels: list[ChannelOut]
    latest: SampleOut | None

    @classmethod
    def of(cls, source: Source, latest: Sample | None) -> SourceOut:
        return cls(
            name=str(source.name),
            channels=[ChannelOut.of(c) for c in source.channels],
            latest=None if latest is None else SampleOut.of(latest),
        )


class ReadingOut(BaseModel):
    time_ns: int
    value: float


class LoopOut(BaseModel):
    """A loop as a client sees it. Separate from `LoopView` so the wire shape stays stable."""

    name: str
    channel: ChannelOut
    default: bool
    mode: str
    law: SerializeAsAny[ControlLawView] | None
    reference: float | str | None
    correction: float
    demand: float | None
    expected: float | None
    delivered_correction: float | None
    reading: ReadingOut | None

    @classmethod
    def of(cls, channel: Channel, loop: Loop[Any], default: bool) -> LoopOut:
        return cls.of_view(channel, loop.view, default)

    @classmethod
    def of_view(cls, channel: Channel, view: LoopView, default: bool) -> LoopOut:
        """From a snapshot, so the wire model can be built off the control thread."""
        reference = view.reference
        return cls(
            name=view.name,
            channel=ChannelOut.of(channel),
            default=default,
            mode=view.mode.value,
            law=view.law,
            reference=reference
            if isinstance(reference, float | int | type(None))
            else reference.tag,
            correction=view.correction,
            demand=view.demand,
            expected=view.expected,
            delivered_correction=view.delivered_correction,
            reading=None
            if view.reading is None
            else ReadingOut(time_ns=view.reading.time_ns, value=view.reading.value),
        )


# endregion
