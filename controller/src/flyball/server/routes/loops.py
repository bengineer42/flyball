"""Making, driving and removing loops on the live rig.

Reading loops is under ``/api/loops`` in ``rig.py``; this adds the verbs. A
loop is a channel regulated through an actuator by a law; ``schema`` says
which channels each actuator can drive -- the loop hands demands over in the
channel's unit, so an actuator with a demand unit takes only channels in
that unit, and one without takes any.
"""

from __future__ import annotations

from typing import Annotated, Any, Union

from fastapi import APIRouter
from pydantic import BaseModel, Field, TypeAdapter

from flyball.control import ControlLaws
from flyball.control.types import Transfer, ValueSource
from flyball.core.errors import ConflictError, NotFoundError
from flyball.core.reading import Measurand, Source
from flyball.core.typing import Positive
from flyball.server.deps import RigDep
from flyball.server.schemas import ChannelOut, LoopOut

router = APIRouter(prefix="/api/loops", tags=["loops"])

LawConfig = Annotated[  # type: ignore[valid-type]
    Union[tuple(law.config for law in ControlLaws.values())],  # ruff: ignore[non-pep604-annotation-union]
    Field(discriminator="tag"),
]


class NewLoop(BaseModel):
    """What makes a loop: ``channel`` is ``source.measurand``; ``law`` a config or tuning name."""

    channel: str
    actuator: str
    law: LawConfig | str | None = None  # type: ignore[valid-type]
    default: bool = False
    min_period_s: Positive | None = None


class Regulate(BaseModel):
    """Aim and hand control to the law."""

    at: float | ValueSource
    tuning: LawConfig | str | None = None  # type: ignore[valid-type]
    transfer: Transfer = Transfer.TRACK


class Reference(BaseModel):
    at: float | ValueSource


class ActuatorChoice(BaseModel):
    name: str
    type: str
    demand_unit: str | None
    """None: takes any channel. Else only channels in this unit."""
    channels: list[str]
    """The channels this actuator may regulate, as ``source.measurand``."""


class LoopSchema(BaseModel):
    """Everything a form needs to make a loop on this rig, right now."""

    channels: list[ChannelOut]
    actuators: list[ActuatorChoice]
    laws: dict[str, Any]
    """JSON Schema of the law config union, discriminated on ``tag``."""
    tunings: list[str]
    """Stored tunings a loop may name instead of a config."""
    regulated: dict[str, str]
    """Channels already regulated, and by which loop."""


def _channel(name: str):
    source_name, _, measurand_name = name.partition(".")
    try:
        return Source.get(source_name)[Measurand.get(measurand_name)]
    except NotFoundError as e:
        raise NotFoundError(f"Channel {name!r} not found") from e


def _out(rig: RigDep, name: str) -> LoopOut:
    loop = rig.loops.resolve(name)
    return LoopOut.of(rig.loops.channel(name), loop, name == rig.loops.default)


@router.get("/schema")
async def read_loop_schema(rig: RigDep) -> LoopSchema:
    channels = [ChannelOut.of(ch) for source in rig.sources for ch in source.channels]
    by_unit: dict[str | None, list[str]] = {}
    for c in channels:
        by_unit.setdefault(c.unit, []).append(f"{c.source}.{c.measurand}")
    every = [f"{c.source}.{c.measurand}" for c in channels]
    actuators = [
        ActuatorChoice(
            name=a.name,
            type=type(a).__name__,
            demand_unit=a.demand_unit.symbol if a.demand_unit is not None else None,
            channels=every if a.demand_unit is None else by_unit.get(a.demand_unit.symbol, []),
        )
        for a in rig.actuators.values()
    ]
    return LoopSchema(
        channels=channels,
        actuators=actuators,
        laws=TypeAdapter(LawConfig).json_schema(),
        tunings=list(rig.tunings.all()),
        regulated={ch.name: loop.name for ch, loop in rig.loops.entries()},
    )


@router.post("", status_code=201)
def make_loop(rig: RigDep, body: NewLoop) -> LoopOut:
    """Attach a loop. 409 if the channel is already regulated or the units disagree."""
    try:
        actuator = rig.actuators[body.actuator]
    except KeyError as e:
        raise NotFoundError(f"Actuator {body.actuator!r} not found") from e
    if body.actuator in rig.loops:
        raise ConflictError(f"{body.actuator!r} already drives a loop; remove it first")
    law = body.law.build() if isinstance(body.law, BaseModel) else body.law  # type: ignore[union-attr]
    with rig.lock:
        rig.attach_loop(
            _channel(body.channel),
            actuator,
            law=law,
            default=body.default,
            min_period_s=body.min_period_s,
        )
    return _out(rig, body.actuator)


@router.delete("/{name}", status_code=204)
def remove_loop(rig: RigDep, name: str) -> None:
    """Detach a loop. It is put in manual first so the actuator holds its last demand."""
    with rig.lock:
        rig.loops.resolve(name).manual()
        rig.loops.remove(name)


@router.post("/{name}/regulate")
def regulate(rig: RigDep, name: str, body: Regulate) -> LoopOut:
    """Aim at ``at`` (a value, or ``process``/``setpoint``/``demand``) and let the law drive."""
    loop = rig.loops.resolve(name)
    tuning = body.tuning.build() if isinstance(body.tuning, BaseModel) else body.tuning  # type: ignore[union-attr]
    if isinstance(tuning, str) and (tuning := rig.tunings.get(tuning)) is None:
        raise NotFoundError(f"Tuning {body.tuning!r} not found")
    with rig.lock:
        loop.regulate(body.at, tuning=tuning, transfer=body.transfer)
        rig.apply(loop.actuator)
    return _out(rig, name)


@router.post("/{name}/manual")
def manual(rig: RigDep, name: str) -> LoopOut:
    """Stop regulating; the actuator keeps its last demand and takes commands directly."""
    with rig.lock:
        rig.loops.resolve(name).manual()
    return _out(rig, name)


@router.put("/{name}/reference")
def set_reference(rig: RigDep, name: str, body: Reference) -> LoopOut:
    """Move the setpoint without touching the mode or the law's state."""
    with rig.lock:
        rig.loops.resolve(name).set_reference(body.at)
    return _out(rig, name)
