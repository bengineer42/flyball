"""The live rig: what it is made of and what it is doing now.

Read-only except for tunings. Everything here reports the rig's own state;
nothing touches hardware on the event loop. Changes to what the rig is doing
go through commands, not through these routes.
"""

from __future__ import annotations

from typing import Annotated, Any, Union

from fastapi import APIRouter
from pydantic import BaseModel, Field, SerializeAsAny

from humctrl.control import ControlLawConfig, ControlLaws, ControlLawView, Loop, Tuning
from humctrl.control.errors import TuningNotRegisteredError
from humctrl.core.errors import NotFoundError
from humctrl.core.reading import Channel, Quantity, Sample, Source
from humctrl.server.deps import RigDep

router = APIRouter(prefix="/api", tags=["rig"])

# A tuning body is any registered law's config, told apart by its tag. Built
# from the registry so a law added by a package is accepted without a change
# here.
LawConfig = Annotated[  # type: ignore[valid-type]
    Union[tuple(law.config for law in ControlLaws.values())],  # ruff: ignore[non-pep604-annotation-union]
    Field(discriminator="tag"),
]

# region Wire models


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


def _channel(rig: RigDep, name: str) -> Channel:
    """``source.quantity`` -> the live channel, or 404."""
    source_name, _, quantity_name = name.partition(".")
    try:
        return Source.get(source_name)[Quantity.get(quantity_name)]
    except NotFoundError as e:
        raise NotFoundError(f"Channel {name!r} not found") from e


# region Sources and readers


@router.get("/sources")
async def read_sources(rig: RigDep) -> list[SourceOut]:
    """Every source a reader has delivered from, with its latest sample."""
    return [SourceOut.of(source, sample) for source, sample in rig._samples.items()]


@router.get("/sources/{name}")
async def read_source(rig: RigDep, name: str) -> SourceOut:
    source = Source.get(name)
    return SourceOut.of(source, rig._samples.get(source))


@router.get("/sources/{name}/{quantity}")
async def read_channel_latest(rig: RigDep, name: str, quantity: str) -> dict[str, Any]:
    """The latest reading on one channel. 404 until the first delivery."""
    channel = _channel(rig, f"{name}.{quantity}")
    if (reading := rig._readings.get(channel)) is None:
        raise NotFoundError(f"No reading yet on {channel.name}")
    return {"channel": ChannelOut.of(channel), "time_ns": reading.time_ns, "value": reading.value}


@router.get("/readers")
async def read_readers(rig: RigDep) -> list[ReaderOut]:
    return [
        ReaderOut(sources=[str(s.name) for s in reader.sources], period_s=loop.loop_time)
        for reader, loop in rig._readers.periodic.items()
    ]


# endregion

# region Loops


@router.get("/loops")
async def read_loops(rig: RigDep) -> list[LoopOut]:
    return [LoopOut.of(ch, loop, ch is rig.loops.default) for ch, loop in rig.loops.items()]  # type: ignore[no-untyped-call]


@router.get("/loops/default")
async def read_default_loop(rig: RigDep) -> LoopOut:
    loop = rig.loops.resolve()
    ch = rig.loops.default
    assert ch is not None
    return LoopOut.of(ch, loop, True)


@router.get("/loops/{channel}")
async def read_loop(rig: RigDep, channel: str) -> LoopOut:
    """``channel`` is ``source.quantity``, the loop's controlled variable."""
    ch = _channel(rig, channel)
    return LoopOut.of(ch, rig.loops.resolve(ch), ch is rig.loops.default)


# endregion

# region Tunings


@router.get("/tunings")
async def read_tunings(rig: RigDep) -> dict[str, SerializeAsAny[ControlLawConfig | ControlLawView]]:
    return rig.tunings.all()


@router.get("/tunings/{tag}")
async def read_tuning(rig: RigDep, tag: str) -> SerializeAsAny[ControlLawConfig | ControlLawView]:
    if (tuning := rig.tunings.get(tag)) is None:
        raise TuningNotRegisteredError(tag)
    return tuning


@router.put("/tunings/{tag}")
async def set_tuning(rig: RigDep, tag: str, body: LawConfig) -> Tuning:  # type: ignore[valid-type]
    """Store ``body`` under ``tag`` on the live rig, replacing any tuning already there."""
    tuning = Tuning(tag=tag, config=body)
    with rig.lock:
        rig.tunings.add(tuning)
    return tuning


# endregion
