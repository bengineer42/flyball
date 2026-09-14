"""Wire format shared across routes.

Deliberately separate from the domain types: the HTTP surface should be free
to change shape without dragging the control code with it, and vice versa.
``Duration`` and ``Rate`` carry integer nanoseconds a browser cannot hold in a
float, so they cross as their parts. Law configs cross as a union told apart
by ``tag``, built from the registry so a law added by a package is accepted
without a change here. Application-specific requests -- pump flows, blends --
live with that application, not here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Union

from pydantic import BaseModel, Field, SerializeAsAny, TypeAdapter

from humctrl.control import ControlLaws, ControlLawView, Loop
from humctrl.core.clock import Duration, Rate, TimeUnit
from humctrl.core.reading import Channel, Sample, Source


def discriminated_union[T](
    members: dict[str, type[T]],
    discriminator: str,
    parser: Callable[[type[T]], type[Any]] = lambda x: x,
) -> Any:
    """One model per registry entry, discriminated by the tag field."""
    return Annotated[
        Union[tuple(parser(member) for member in members.values())],  # ruff: ignore[non-pep604-annotation-union]
        Field(discriminator=discriminator),
    ]


LawConfig = discriminated_union(ControlLaws, "tag", lambda law: law.config)
LawsSchema = TypeAdapter(LawConfig).json_schema()


class DurationRequest(BaseModel):
    seconds: int
    nanoseconds: int = 0

    def parse(self) -> Duration:
        return Duration(self.seconds, self.nanoseconds)


class RateRequest(BaseModel):
    per: TimeUnit
    value: float

    def parse(self) -> Rate:
        return Rate(self.value, self.per)


# region Live rig


class ChannelOut(BaseModel):
    source: str
    quantity: str
    unit: str
    label: str

    @classmethod
    def of(cls, channel: Channel) -> ChannelOut:
        return cls(
            source=str(channel.source.name),
            quantity=channel.quantity.name,
            unit=channel.unit,
            label=channel.quantity.label,
        )


class SampleOut(BaseModel):
    seq: int
    time_ns: int
    values: dict[str, float]

    @classmethod
    def of(cls, sample: Sample) -> SampleOut:
        return cls(
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


class ReaderOut(BaseModel):
    sources: list[str]
    period_s: float


class ReadingOut(BaseModel):
    time_ns: int
    value: float


class LoopOut(BaseModel):
    """A loop as a client sees it: identity, what it is doing, and the law in force.

    Built here rather than returning ``LoopView`` so the wire shape is the
    server's to keep stable while the loop's internals move.
    """

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
        reference = loop.reference
        return cls(
            name=loop.name,
            channel=ChannelOut.of(channel),
            default=default,
            mode=loop.mode.value,
            law=None if loop.law is None else loop.law.view,
            reference=reference
            if isinstance(reference, float | int | type(None))
            else reference.tag,
            correction=loop.correction,
            demand=loop.demand,
            expected=loop.expected,
            delivered_correction=loop.delivered_correction,
            reading=None
            if loop.reading is None
            else ReadingOut(time_ns=loop.reading.time_ns, value=loop.reading.value),
        )


# endregion
