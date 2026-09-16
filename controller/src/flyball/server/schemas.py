"""Wire format shared across routes.

Separate from the domain types so the HTTP surface and the control code can
change shape independently. Law configs cross as a `tag`-discriminated union
built from the registry. Application-specific requests live with the
application.

Addresses are the only names on the wire: a signal's, a node's, a
controller's (its target's). The rig resolves them once at the boundary.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, SerializeAsAny, TypeAdapter

from flyball.control import (
    ControlLaws,
    ControlLawView,
    Controller,
    ControllerState,
    ControllerView,
    FeedforwardConfig,
)
from flyball.core.clock import Clock
from flyball.core.device import CommandSpec, Condition, Device
from flyball.core.model import discriminated_union
from flyball.core.signal import Node, Reading, Sample, Signal, WriteState
from flyball.runtime.polling import DeviceRun

LawConfig = discriminated_union(ControlLaws, "tag", lambda law: law.config)
LawsSchema = TypeAdapter(LawConfig).json_schema()

ANY = TypeAdapter(Any)


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


# endregion

# region Values


class ReadingOut(BaseModel):
    """One value on one signal at one instant."""

    signal: str
    time_ns: int
    value: float

    @classmethod
    def of(cls, reading: Reading) -> ReadingOut:
        return cls(signal=reading.signal.address, time_ns=reading.time_ns, value=reading.value)


class SampleOut(BaseModel):
    """Signals under one node at one instant; `values` keyed relative to `node`."""

    node: str
    time_ns: int
    values: dict[str, float]

    @classmethod
    def of(cls, sample: Sample) -> SampleOut:
        return cls(node=sample.node.address, time_ns=sample.time_ns, values=sample.by_name())


class LatestOut(BaseModel):
    """The last reading on a signal, without repeating its address."""

    time_ns: int
    value: float


class WriteOut(BaseModel):
    """What a W signal was last set to, after limits, and by whom."""

    value: float | None
    requested: float | None = None
    """What was asked for, when the clamp changed it."""
    at_limit: Literal["low", "high"] | None = None
    controller: str | None = None
    """The controller driving it; it refuses manual demands."""

    @classmethod
    def of(cls, state: WriteState) -> WriteOut:
        return cls(
            value=state.value,
            requested=state.requested,
            at_limit=state.at_limit,
            controller=state.controller,
        )


def writes_out(states: dict[Signal, WriteState] | Any) -> dict[str, WriteOut]:
    """Write states by address: what a demand answers with."""
    return {signal.address: WriteOut.of(state) for signal, state in states.items()}


# endregion

# region Devices


class SignalOut(BaseModel):
    """One signal of a device's tree, with its metadata as in force and its latest values.

    `access` is the set after any rig-file restriction, as letters (`"rp"`).
    `latest` is the last reading, when there has been one; `write` the last
    committed state of a writable signal.
    """

    name: str
    address: str
    access: str
    label: str
    quantity: str
    unit: str
    dimension: str | None = None
    """The unit's dimension, so a client can tell what may drive or be compared with what."""
    dtype: str
    shape: list[int]
    range: tuple[float, float] | None = None
    precision: int | None = None
    warn: tuple[float, float] | None = None
    alarm: tuple[float, float] | None = None
    poll_s: float | None = None
    limits: tuple[float, float] | None = None
    together: list[str]
    latest: LatestOut | None = None
    write: WriteOut | None = None

    @classmethod
    def of(cls, signal: Signal, latest: Reading | None, write: WriteState | None) -> SignalOut:
        spec = signal.spec
        return cls(
            name=signal.name,
            address=signal.address,
            access=str(signal.access),
            label=spec.label,
            quantity=spec.quantity.name,
            unit=signal.unit.symbol,
            dimension=signal.unit.dimension.label,
            dtype=spec.dtype,
            shape=list(spec.shape),
            range=spec.range,
            precision=spec.precision,
            warn=spec.warn,
            alarm=spec.alarm,
            poll_s=signal.poll_s,
            limits=spec.limits,
            together=sorted(spec.together),
            latest=None
            if latest is None
            else LatestOut(time_ns=latest.time_ns, value=latest.value),
            write=None if write is None else WriteOut.of(write),
        )


class NamespaceOut(BaseModel):
    """A namespace of a device's tree: a sub-device or grouping, with what is under it."""

    name: str
    address: str
    atomic: bool
    label: str
    poll_s: float | None = None
    signals: list[SignalOut | NamespaceOut]


def tree_out(
    node: Node, latest: dict[Signal, Reading], written: dict[Signal, WriteState]
) -> list[SignalOut | NamespaceOut]:
    """The signals and namespaces directly under `node`, recursing into the namespaces."""
    out: list[SignalOut | NamespaceOut] = [
        SignalOut.of(signal, latest.get(signal), written.get(signal))
        for signal in node.signals.values()
    ]
    out.extend(
        NamespaceOut(
            name=child.name,
            address=child.address,
            atomic=child.atomic,
            label=child.label,
            poll_s=child.poll_s,
            signals=tree_out(child, latest, written),
        )
        for child in node.children.values()
    )
    return out


class CommandOut(BaseModel):
    name: str
    description: str | None = None
    simulation: bool = False

    @classmethod
    def of(cls, spec: CommandSpec) -> CommandOut:
        return cls(name=spec.tag, description=spec.doc, simulation=spec.simulation)


class RunOut(BaseModel):
    """How the runtime is polling a device; absent when nothing on it is polled."""

    period_s: float | None
    running: bool
    last_read_ns: int | None

    @classmethod
    def of(cls, run: DeviceRun) -> RunOut:
        return cls(period_s=run.period_s, running=run.running, last_read_ns=run.last_read_ns)


class DeviceOut(BaseModel):
    """One entry of `GET /api/devices`: the tree with live values, commands, state, conditions.

    `driver` is the rig file's tag for it, or null for a device built in
    code; `type` its class; `kind` what claimed its name -- `device`, or
    `simulation` for an application's own simulation device, which a UI
    keeps on its simulation page. `conditions` joins what the device
    reports of itself with what the runtime knows of polling it (`offline`,
    `slow`).
    """

    name: str
    label: str | None = None
    kind: str
    driver: str | None = None
    type: str
    link: str | None = None
    poll_s: float | None = None
    signals: list[SignalOut | NamespaceOut]
    commands: list[CommandOut]
    state: Any
    conditions: list[Condition]
    run: RunOut | None = None

    @classmethod
    def of(
        cls,
        device: Device,
        *,
        kind: str,
        latest: dict[Signal, Reading],
        link: str | None,
        run: DeviceRun | None,
    ) -> DeviceOut:
        state = device.state
        conditions = list(state.conditions)
        if run is not None:
            conditions.extend(run.conditions)
        return cls(
            name=device.name,
            label=device.label,
            kind=kind,
            driver=type(device.config).config_tag,
            type=type(device).__name__,
            link=link,
            poll_s=device.poll_s,
            signals=tree_out(device.root, latest, device.written),
            commands=[CommandOut.of(spec) for spec in type(device).commands.values()],
            state=ANY.dump_python(state, mode="json"),
            conditions=conditions,
            run=None if run is None else RunOut.of(run),
        )


# endregion

# region Controllers


class ControllerOut(BaseModel):
    """A controller as a client sees it. Separate from `ControllerView` so the wire stays stable.

    Named by `target`, the writable signal it drives; `source` is the
    publishing signal it regulates. `demand`, `expected` and `correction`
    are in `demand_unit`, the target's.
    """

    name: str
    label: str | None = None
    """The target signal's display name; None: show `name`."""
    target: str
    source: str
    default: bool
    mode: str
    law: SerializeAsAny[ControlLawView] | None
    feedforward: SerializeAsAny[FeedforwardConfig]
    """What maps the setpoint to the demand; the law's correction is added to it."""
    demand_unit: str
    reference: float | str | None
    setpoint: float | None
    """The reference as resolved at the last tick, so a ramp's current value is on the wire."""
    correction: float
    demand: float | None
    expected: float | None
    delivered_correction: float | None
    reading: ReadingOut | None

    @classmethod
    def of(
        cls, controller: Controller, default: bool, state: ControllerState | None = None
    ) -> ControllerOut:
        """From the controller now, or from `state` (a tick's snapshot) joined to its settings."""
        view = controller.view if state is None else ControllerView.of(controller.settings, state)
        reference = view.reference
        return cls(
            name=view.name,
            label=controller.target.label or None,
            target=controller.target.address,
            source=controller.source.address,
            default=default,
            mode=view.mode.value,
            law=view.law,
            feedforward=view.feedforward,
            demand_unit=view.demand_unit or controller.target.unit.symbol,
            reference=reference
            if isinstance(reference, float | int | type(None))
            else reference.tag,
            setpoint=view.setpoint,
            correction=view.correction,
            demand=view.demand,
            expected=view.expected,
            delivered_correction=view.delivered_correction,
            reading=None if view.reading is None else ReadingOut.of(view.reading),
        )


# endregion
