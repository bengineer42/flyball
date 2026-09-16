"""Controllers: reading them, and making, driving and removing them on the live rig.

A controller binds one publishing signal (`source`) to one writable signal
(`target`) through a law and a feedforward, and is named by its target's
address, so `/{address}` carries dots (`heaters.heater1`). `schema` says
what a form needs: every P signal a controller may regulate and every W
signal it may drive, with units and dimensions, the law and feedforward
unions, the stored tunings, and which signals are already spoken for.
"""

from __future__ import annotations

from typing import Annotated, Any, Union

from fastapi import APIRouter
from pydantic import BaseModel, Field, TypeAdapter

from flyball.control import ControlLaws, Controller, Feedforwards, SetPointGenerators
from flyball.control.errors import LastReadingNotAvailableError
from flyball.control.types import Transfer, ValueSource
from flyball.core.errors import NotFoundError
from flyball.core.signal import Access, Signal
from flyball.core.typing import Positive
from flyball.runtime.rig import Rig
from flyball.server.deps import RigDep
from flyball.server.schemas import ControllerOut

router = APIRouter(prefix="/api/controllers", tags=["controllers"])

LawConfig = Annotated[  # type: ignore[valid-type]
    Union[tuple(law.config for law in ControlLaws.values())],  # ruff: ignore[non-pep604-annotation-union]
    Field(discriminator="tag"),
]
FeedforwardConfig = Annotated[  # type: ignore[valid-type]
    Union[tuple(ff.config for ff in Feedforwards.values())],  # ruff: ignore[non-pep604-annotation-union]
    Field(discriminator="tag"),
]
GeneratorConfig = Annotated[  # type: ignore[valid-type]
    Union[tuple(g.config for g in SetPointGenerators.values())],  # ruff: ignore[non-pep604-annotation-union]
    Field(discriminator="tag"),
]
"""Built here from the registry, as the law and feedforward unions are; `profile` is in it, and
its own segments are `flyball.control.GeneratorConfig`, the same union closed at import."""


class NewController(BaseModel):
    """What makes a controller: two addresses, and a law as a config or a tuning's name."""

    target: str
    """The W signal to drive; the controller's name."""
    source: str
    """The P signal to regulate."""
    law: LawConfig | str | None = None  # type: ignore[valid-type]
    feedforward: FeedforwardConfig | str | None = None  # type: ignore[valid-type]
    """A config or a tag. Omitted: ``setpoint`` when the units agree, else ``none``."""
    default: bool = False
    min_period_s: Positive | None = None


class Regulate(BaseModel):
    """Aim and hand control to the law."""

    at: float | ValueSource | GeneratorConfig  # type: ignore[valid-type]
    start: float | ValueSource | None = None
    """Where a generator starts from: a value, `setpoint`, `process` (the reading) or `demand`.
    Omitted: the current setpoint while regulating, else the last reading."""
    tuning: LawConfig | str | None = None  # type: ignore[valid-type]
    transfer: Transfer = Transfer.TRACK


class Reference(BaseModel):
    at: float | ValueSource | GeneratorConfig  # type: ignore[valid-type]
    start: float | ValueSource | None = None
    """Where a generator starts from; see `Regulate.start`."""


class SignalChoice(BaseModel):
    """A signal a controller may bind to, with what a form shows beside it."""

    address: str
    device: str
    label: str
    unit: str
    dimension: str | None
    range: tuple[float, float] | None = None
    """A P signal's plausible values, for a setpoint entry."""
    limits: tuple[float, float] | None = None
    """A W signal's clamp, for a demand entry."""

    @classmethod
    def of(cls, signal: Signal) -> SignalChoice:
        return cls(
            address=signal.address,
            device=signal.device.name,
            label=signal.label,
            unit=signal.unit.symbol,
            dimension=signal.unit.dimension.label,
            range=signal.spec.range,
            limits=signal.limits,
        )


class TuningChoice(BaseModel):
    name: str
    law: str
    """The law's tag, so a form can offer the tunings for one law."""
    config: dict[str, Any]


class ControllerSchema(BaseModel):
    """Everything a form needs to make a controller on this rig, right now."""

    sources: list[SignalChoice]
    """Every publishing signal."""
    targets: list[SignalChoice]
    """Every writable signal."""
    laws: dict[str, Any]
    """JSON Schema of the law config union, discriminated on ``tag``."""
    feedforwards: dict[str, Any]
    """JSON Schema of the feedforward config union, discriminated on ``tag``."""
    generators: dict[str, Any]
    """JSON Schema of the set-point generator config union, discriminated on ``tag``."""
    tunings: list[TuningChoice]
    """Stored tunings a controller may name instead of a config."""
    regulated: dict[str, str]
    """Sources already regulated, and by which controller."""
    driven: dict[str, str]
    """Targets already driven, and by which controller."""


def _out(rig: Rig, name: str | None = None) -> ControllerOut:
    controller = rig.controllers.resolve(name)
    return ControllerOut.of(controller, controller.name == rig.controllers.default)


def _signal(rig: Rig, address: str) -> Signal:
    """The signal at `address`, or 404. Whether it may be bound is the rig's to refuse."""
    target = rig.resolve(address)
    if not isinstance(target, Signal):
        raise NotFoundError(f"'{address}' is a namespace, not a signal")
    return target


def _start(controller: Controller, start: float | ValueSource | None) -> float | ValueSource:
    """The request's `start` as given (the controller resolves a `ValueSource`), else the default rule."""
    return _generator_start(controller) if start is None else start


def _generator_start(controller: Controller) -> float:
    """Where a generator spec starts from: the controller's own setpoint, or its last reading.

    The same rule the `ramp` program step uses in `programmer/loops.py`.

    Raises:
        LastReadingNotAvailableError: Neither is available yet.
    """
    start = (
        controller.setpoint_at(controller.clock.now_ns())
        if controller.reference is not None
        else controller.last_value
    )
    if start is None:
        raise LastReadingNotAvailableError
    return start


@router.get("")
async def read_controllers(rig: RigDep) -> list[ControllerOut]:
    return [
        ControllerOut.of(c, name == rig.controllers.default) for name, c in rig.controllers.items()
    ]


@router.get("/schema")
async def read_controller_schema(rig: RigDep) -> ControllerSchema:
    signals = [s for device in rig.devices.values() for s in device.signals.values()]
    return ControllerSchema(
        sources=[SignalChoice.of(s) for s in signals if Access.P in s.access],
        targets=[SignalChoice.of(s) for s in signals if Access.W in s.access],
        laws=TypeAdapter(LawConfig).json_schema(),
        feedforwards=TypeAdapter(FeedforwardConfig).json_schema(),
        generators=TypeAdapter(GeneratorConfig).json_schema(),
        tunings=[
            TuningChoice(name=name, law=config.tag, config=config.model_dump(mode="json"))
            for name, config in rig.tunings.all().items()
        ],
        regulated={source.address: c.name for source, c in rig.controllers.entries()},
        driven={name: name for name in rig.controllers},
    )


@router.get("/default")
async def read_default_controller(rig: RigDep) -> ControllerOut:
    return _out(rig)


@router.get("/{address}")
async def read_controller(rig: RigDep, address: str) -> ControllerOut:
    """`address` is the controller's name: its target's."""
    return _out(rig, address)


@router.post("", status_code=201)
def make_controller(rig: RigDep, body: NewController) -> ControllerOut:
    """Attach a controller. 409 if a signal is already spoken for or the units disagree."""
    target = _signal(rig, body.target)
    source = _signal(rig, body.source)
    law = body.law.build() if isinstance(body.law, BaseModel) else body.law  # type: ignore[union-attr]
    with rig.lock:
        controller = rig.attach_controller(
            target,
            source,
            law=law,
            feedforward=body.feedforward,
            default=body.default,
            min_period_s=body.min_period_s,
        )
    return _out(rig, controller.name)


@router.delete("/{address}", status_code=204)
def remove_controller(rig: RigDep, address: str) -> None:
    """Detach a controller. It is put in manual first so the target holds its last demand."""
    with rig.lock:
        rig.controllers.resolve(address).manual()
        rig.detach_controller(address)


@router.post("/{address}/regulate")
def regulate(rig: RigDep, address: str, body: Regulate) -> ControllerOut:
    """Aim at ``at`` and let the law drive.

    ``at`` is a value, ``process``/``setpoint``/``demand``, or a generator
    spec, which starts from the controller's current setpoint or reading.
    The handover's demand is committed to the target's device at once.
    """
    controller = rig.controllers.resolve(address)
    tuning = body.tuning.build() if isinstance(body.tuning, BaseModel) else body.tuning  # type: ignore[union-attr]
    if isinstance(tuning, str) and (tuning := rig.tunings.get(tuning)) is None:
        raise NotFoundError(f"Tuning {body.tuning!r} not found")
    generator = body.at.build() if isinstance(body.at, BaseModel) else None  # type: ignore[union-attr]
    at = _start(controller, body.start) if generator is not None else body.at
    with rig.lock:
        controller.regulate(at, generator=generator, tuning=tuning, transfer=body.transfer)  # type: ignore[arg-type]
    return _out(rig, address)


@router.post("/{address}/manual")
def manual(rig: RigDep, address: str) -> ControllerOut:
    """Stop regulating; the target keeps its last demand and takes demands directly."""
    with rig.lock:
        rig.controllers.resolve(address).manual()
    return _out(rig, address)


@router.put("/{address}/reference")
def set_reference(rig: RigDep, address: str, body: Reference) -> ControllerOut:
    """Move the setpoint, or start following a generator, without touching the mode."""
    controller = rig.controllers.resolve(address)
    generator = body.at.build() if isinstance(body.at, BaseModel) else None  # type: ignore[union-attr]
    at = _start(controller, body.start) if generator is not None else body.at
    with rig.lock:
        controller.set_reference(at, generator=generator)  # type: ignore[arg-type]
    return _out(rig, address)
