"""Wire format shared across routes.

Deliberately separate from the domain types: the HTTP surface should be free
to change shape without dragging the control code with it, and vice versa.
``Duration`` and ``Rate`` carry their own wire forms (see ``core.clock``). Law
configs cross as a union told apart
by ``tag``, built from the registry so a law added by a package is accepted
without a change here. Application-specific requests -- pump flows, blends --
live with that application, not here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Union

from pydantic import BaseModel, Field, SerializeAsAny, TypeAdapter

from flyball.control import ControlLaws, ControlLawView, Loop, LoopView
from flyball.core.clock import Clock
from flyball.core.reading import Channel, Sample, Source


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


# region Live rig


class ClockOut(BaseModel):
    """The rig's timebase, so a client can place its own clock against the rig's.

    ``now_ns`` is the rig's wall-clock reading at the moment of the request;
    ``elapsed_ns`` is how long the rig has been up. A client that records
    ``now_ns`` against its own clock can convert any telemetry timestamp.
    """

    start_time_ns: int
    now_ns: int
    elapsed_ns: int
    tags: dict[str, int]

    @classmethod
    def of(cls, clock: Clock) -> ClockOut:
        return cls(
            start_time_ns=clock.start_time_ns,
            now_ns=clock.now_ns(),
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

    @classmethod
    def of(cls, channel: Channel) -> ChannelOut:
        return cls(
            source=str(channel.source.name),
            measurand=channel.measurand.name,
            unit=channel.unit.symbol,
            label=channel.measurand.label,
            range=channel.measurand.range,
            precision=channel.measurand.precision,
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
