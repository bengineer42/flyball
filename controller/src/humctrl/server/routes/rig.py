"""The live rig: what it is made of and what it is doing now.

Read-only except for tunings. Everything here reports the rig's own state;
nothing touches hardware on the event loop. Changes to what the rig is doing
go through commands, not through these routes.
"""

from __future__ import annotations

from typing import Annotated, Any, Union

from fastapi import APIRouter
from pydantic import Field, SerializeAsAny

from humctrl.control import ControlLawConfig, ControlLaws, ControlLawView, Tuning
from humctrl.control.errors import TuningNotRegisteredError
from humctrl.core.errors import NotFoundError
from humctrl.core.reading import Channel, Quantity, Source
from humctrl.server.deps import RigDep
from humctrl.server.schemas import ChannelOut, LoopOut, ReaderOut, SourceOut

router = APIRouter(prefix="/api", tags=["rig"])

# A tuning body is any registered law's config, told apart by its tag. Built
# from the registry so a law added by a package is accepted without a change
# here.
LawConfig = Annotated[  # type: ignore[valid-type]
    Union[tuple(law.config for law in ControlLaws.values())],  # ruff: ignore[non-pep604-annotation-union]
    Field(discriminator="tag"),
]


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
    return [
        LoopOut.of(ch, loop, loop.name == rig.loops.default) for ch, loop in rig.loops.entries()
    ]


@router.get("/loops/default")
async def read_default_loop(rig: RigDep) -> LoopOut:
    loop = rig.loops.resolve()
    return LoopOut.of(rig.loops.channel(loop.name), loop, True)


@router.get("/loops/{name}")
async def read_loop(rig: RigDep, name: str) -> LoopOut:
    """``name`` is the loop's -- which is its actuator's."""
    loop = rig.loops.resolve(name)
    return LoopOut.of(rig.loops.channel(name), loop, name == rig.loops.default)


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
