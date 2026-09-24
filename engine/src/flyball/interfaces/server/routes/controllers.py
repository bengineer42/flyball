"""Controllers: reading them, and making, driving and removing them on the live rig.

A controller binds one published signal (`measured`) to one demand (its
`output`) through a law and a feedforward, and is named by its output's
address, so `/{address}` carries dots (`heaters.heater1`). `schema` says
what a form needs: every P signal a controller may regulate and every
writable demand it may drive (a setting never is one), with units and
dimensions, the law and feedforward unions, the stored tunings, and which
signals are already spoken for.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, TypeAdapter

from flyball.control import Affine, GeneratorConfig, Table
from flyball.control.errors import LastReadingNotAvailableError
from flyball.control.setpoint import generator_union
from flyball.foundation.config import discriminated_union
from flyball.foundation.device import Access, Role, Signal
from flyball.foundation.errors import NotFoundError
from flyball.foundation.typing import Positive
from flyball.interfaces.server.deps import RigDep, current_catalog
from flyball.interfaces.server.routes.stop import actor
from flyball.interfaces.server.schemas import ControllerOut, LawConfig
from flyball.model.controller import Controller, FaultAction, ValueSource
from flyball.model.feedforward import Identity, NoFeedforward
from flyball.model.law import Transfer
from flyball.rig import Rig
from flyball.rig.latches import fault_cause
from flyball.runtime.config import ControllerEntry, OnFaultEntry

router = APIRouter(prefix="/api/controllers", tags=["controllers"])

# `LawConfig` is `interfaces.server.schemas`'s, reused rather than rebuilt.
# `FeedforwardConfig` is every built-in feedforward's, direct -- no extension
# defines one today (`control/configs.py` registers all of them); see that
# module's comment for why this isn't `get_catalog()`/`Catalogs.discover()`.
# `GeneratorConfig` is `flyball.control`'s union over the built-in generators
# (`control/setpoint.py`), which also admits any other the current `Catalogs`
# registered; `profile`'s segments are this same union. `generator_union()`
# is its JSON schema with those others listed too.
_FEEDFORWARDS = (Identity, NoFeedforward, Affine, Table)
FeedforwardConfig = discriminated_union(
    {ff.type: ff for ff in _FEEDFORWARDS}, "type", lambda ff: ff.config_type
)


class NewController(BaseModel):
    """What makes a controller: two addresses, and a law as a config or a tuning's name."""

    output: str
    """The demand to drive; the controller's name."""
    measured: str
    """The P signal to regulate."""
    law: LawConfig | str | None = None  # type: ignore[valid-type]
    feedforward: FeedforwardConfig | str | None = None  # type: ignore[valid-type]
    """A config or a type. Omitted: ``identity`` when the units agree, else ``none``."""
    is_default: bool = False
    min_period_s: Positive | None = None
    setpoint_period_s: Positive | None = None
    """Re-apply a moving setpoint's feedforward this often between readings; omitted:
    `max(0.1 s, poll_s / 4)`."""
    on_fault: OnFaultEntry = "freeze"
    """What it does once its source has been faulty for its wait (the rig file's `on_fault`)."""


class Regulate(BaseModel):
    """Aim and hand control to the law."""

    at: float | ValueSource | GeneratorConfig  # type: ignore[valid-type]
    start: float | ValueSource | None = None
    """Where a generator starts from: a value, `setpoint`, `measured` (the reading) or `output`.
    Omitted: the current setpoint while regulating, else the last reading."""
    tuning: LawConfig | str | None = None  # type: ignore[valid-type]
    transfer: Transfer = Transfer.TRACK


class NewSetpoint(BaseModel):
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
    """A demand's clamp, for an output entry."""

    @classmethod
    def of(cls, signal: Signal) -> SignalChoice:
        return cls(
            address=signal.address,
            device=signal.device.name,
            label=signal.label,
            unit=signal.unit.symbol,
            dimension=signal.unit.dimension.label,
            range=signal.range,
            limits=signal.limits,
        )


class TuningChoice(BaseModel):
    name: str
    law: str
    """The law's type, so a form can offer the tunings for one law."""
    config: dict[str, Any]


class ControllerSchema(BaseModel):
    """Everything a form needs to make a controller on this rig, right now."""

    measured: list[SignalChoice]
    """Every published signal: what a controller may regulate."""
    outputs: list[SignalChoice]
    """Every writable demand (role `demand` and `W`): what a controller may drive."""
    laws: dict[str, Any]
    """JSON Schema of the law config union, discriminated on ``type``."""
    feedforwards: dict[str, Any]
    """JSON Schema of the feedforward config union, discriminated on ``type``."""
    generators: dict[str, Any]
    """JSON Schema of the setpoint generator config union, discriminated on ``type``."""
    tunings: list[TuningChoice]
    """Stored tunings a controller may name instead of a config."""
    regulated: dict[str, str]
    """Measured signals already regulated, and by which controller."""
    driven: dict[str, str]
    """Outputs already driven, and by which controller."""


def drivable(signal: Signal) -> bool:
    """Whether a controller may drive `signal`: a demand, and writable (C13).

    A setting is never a controller's output, however writable; a demand
    only its group drives (the blender's `flows.*`, `RP`) is not writable.
    """
    return signal.role is Role.DEMAND and Access.W in signal.access


def _out(rig: Rig, name: str | None = None) -> ControllerOut:
    controller = rig.controllers.resolve(name)
    return ControllerOut.of(
        controller,
        controller.name == rig.controllers.default_controller,
        latched=[latch.cause for latch in rig.stopping.latches.of_controller(controller)],
    )


def _signal(rig: Rig, address: str) -> Signal:
    """The signal at `address`, or 404. Whether it may be bound is the rig's to refuse."""
    found = rig.resolve(address)
    if not isinstance(found, Signal):
        raise NotFoundError(f"'{address}' is a namespace, not a signal")
    return found


def _start(controller: Controller, start: float | ValueSource | None) -> float | ValueSource:
    """The request's `start` (a `ValueSource` is the controller's to resolve), else the rule."""
    return _generator_start(controller) if start is None else start


def _generator_start(controller: Controller) -> float:
    """Where a generator spec starts from: the controller's own setpoint, or its last reading.

    The same rule the `ramp` program step uses in `sequencing/loops.py`.

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


# The async routes here read the rig on the event loop, so they do not take
# `rig.lock` (a delivery may hold it for a bus transaction). Each iterates a
# snapshot taken in one C-level `list(...)` instead, so a controller or a
# device added or removed meanwhile is not an error.


@router.get("")
async def read_controllers(rig: RigDep) -> list[ControllerOut]:
    default = rig.controllers.default_controller
    latches = rig.stopping.latches
    return [
        ControllerOut.of(c, name == default, latched=[x.cause for x in latches.of_controller(c)])
        for name, c in list(rig.controllers.items())
    ]


@router.get("/schema")
async def read_controller_schema(rig: RigDep) -> ControllerSchema:
    devices = list(rig.devices.values())
    controllers = list(rig.controllers.items())
    signals = [s for device in devices for s in list(device.signals.values())]
    return ControllerSchema(
        measured=[SignalChoice.of(s) for s in signals if Access.P in s.access],
        outputs=[SignalChoice.of(s) for s in signals if drivable(s)],
        laws=TypeAdapter(LawConfig).json_schema(),
        feedforwards=TypeAdapter(FeedforwardConfig).json_schema(),
        generators=TypeAdapter(generator_union(current_catalog())).json_schema(),
        tunings=[
            TuningChoice(name=name, law=config.type, config=config.model_dump(mode="json"))
            for name, config in rig.tunings.all().items()
        ],
        regulated={c.measured_signal.address: c.name for _, c in controllers},
        driven={name: name for name, _ in controllers},
    )


@router.get("/default")
async def read_default_controller(rig: RigDep) -> ControllerOut:
    return _out(rig)


@router.get("/{address}")
async def read_controller(rig: RigDep, address: str) -> ControllerOut:
    """`address` is the controller's name: its output's."""
    return _out(rig, address)


@router.post("", status_code=201)
def make_controller(rig: RigDep, body: NewController) -> ControllerOut:
    """Attach a controller. 409 if a signal is already spoken for or the units disagree."""
    output = _signal(rig, body.output)
    measured = _signal(rig, body.measured)
    law = body.law.build() if isinstance(body.law, BaseModel) else body.law  # type: ignore[union-attr]
    with rig.lock:
        controller = rig.attach_controller(
            output,
            measured,
            law=law,
            feedforward=body.feedforward,
            is_default=body.is_default,
            min_period_s=body.min_period_s,
            setpoint_period_s=body.setpoint_period_s,
            on_fault=ControllerEntry(measured=body.measured, on_fault=body.on_fault).fault_policy(),
        )
    return _out(rig, controller.name)


@router.delete("/{address}", status_code=204)
def remove_controller(rig: RigDep, address: str) -> None:
    """Detach a controller. It is put in manual first so the output holds its last value."""
    with rig.lock:
        rig.controllers.resolve(address).manual()
        rig.detach_controller(address)


@router.post("/{address}/regulate")
def regulate(rig: RigDep, request: Request, address: str, body: Regulate) -> ControllerOut:
    """Aim at ``at`` and let the law drive.

    ``at`` is a value, ``measured``/``setpoint``/``output``, or a generator
    spec, which starts from the controller's current setpoint or reading.
    The handover's output is committed to the output's device at once.

    409 while a latch holds the controller or its output (a stop, an `on_fault`
    action): a person resets it first. The one a person's `regulate` clears itself
    is the controller's own `on_fault: manual` latch, which holds nothing else.
    """
    controller = rig.controllers.resolve(address)
    who = actor(request)
    cause = fault_cause(controller.name)
    latch = rig.stopping.latches.get(cause)
    if (
        who.person
        and latch is not None
        and latch.action == FaultAction.MANUAL.value
        and len(rig.stopping.latches.of_controller(controller)) == 1
    ):
        rig.stopping.reset(cause, who)
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
    """Stop regulating; the output keeps its last value and takes demands directly."""
    with rig.lock:
        rig.controllers.resolve(address).manual()
    return _out(rig, address)


@router.put("/{address}/setpoint")
def set_setpoint(rig: RigDep, address: str, body: NewSetpoint) -> ControllerOut:
    """Move the setpoint, or start following a generator, without touching the mode."""
    controller = rig.controllers.resolve(address)
    generator = body.at.build() if isinstance(body.at, BaseModel) else None  # type: ignore[union-attr]
    at = _start(controller, body.start) if generator is not None else body.at
    with rig.lock:
        controller.set_setpoint(at, generator=generator)  # type: ignore[arg-type]
    return _out(rig, address)
