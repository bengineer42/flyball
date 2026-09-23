"""Wire format shared across routes.

Separate from the domain types so the HTTP surface and the control code can
change shape independently. Law configs cross as a `type`-discriminated union
built from the registry. Application-specific requests live with the
application.

Addresses are the only names on the wire: a signal's, a node's, a
controller's (its output's). The rig resolves them once at the boundary.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    SerializeAsAny,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    model_serializer,
)

from flyball.control import IMC, PI, PID, OnOff, OpenLoop, P, Scheduled, SlidingMode, SmithPredictor
from flyball.foundation.config import discriminated_union
from flyball.foundation.device import (
    CommandSpec,
    Condition,
    Device,
    Input,
    Limit,
    Node,
    Reading,
    Role,
    Sample,
    Signal,
    WriteState,
)
from flyball.foundation.time import Clock
from flyball.model.controller import Controller, ControllerState, ControllerView
from flyball.model.feedforward import FeedforwardConfig
from flyball.model.generator import SetpointGenerator
from flyball.model.law import ControlLawView
from flyball.rig import DeviceRun

# Every built-in law, direct: no extension defines one today
# (`control/configs.py` registers all of them), and building this from
# `get_catalog()`/`Catalogs.discover()` at import time would risk
# `discover()` re-entering a module still mid-import through some
# extension's own import chain -- see `runtime/config.py`'s equivalent
# comment on `LawConfig`/`FeedforwardConfig` there.
_LAWS = (OpenLoop, P, PI, PID, IMC, OnOff, SmithPredictor, Scheduled, SlidingMode)
LawConfig = discriminated_union({law.type: law for law in _LAWS}, "type", lambda law: law.config)
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
    value: Any

    @classmethod
    def of(cls, reading: Reading) -> ReadingOut:
        return cls(signal=reading.signal.address, time_ns=reading.time_ns, value=reading.value)


class WriteMetaOut(BaseModel):
    """A demand's write record, riding along with its reading in a `SampleOut`.

    No `value`: that is already in `values`, keyed the same way.
    """

    requested: float | None = None
    """What was asked for, when the clamp changed it."""
    at_limit: Limit | None = None
    controller: str | None = None
    """The controller driving it; it refuses manual demands."""


class SampleOut(BaseModel):
    """Signals under one node at one instant; `values` keyed relative to `node`.

    `writes` carries the write record (`requested`, `at_limit`, `controller`) for each demand
    the sample includes, keyed the same way as `values` -- `/ws/writes` folded in here.
    """

    node: str
    time_ns: int
    values: dict[str, Any]
    writes: dict[str, WriteMetaOut] = {}

    @classmethod
    def of(cls, sample: Sample, latest: Mapping[Signal, Reading] | None = None) -> SampleOut:
        writes: dict[str, WriteMetaOut] = {}
        if latest is not None:
            for signal in sample.values:
                if signal.role is Role.DEMAND and (reading := latest.get(signal)) is not None:
                    writes[sample.node.relative(signal)] = WriteMetaOut(
                        requested=reading.requested,
                        at_limit=reading.at_limit,
                        controller=reading.controller,
                    )
        return cls(
            node=sample.node.address,
            time_ns=sample.time_ns,
            values=sample.by_name(),
            writes=writes,
        )


class LatestOut(BaseModel):
    """The last reading on a signal, without repeating its address."""

    time_ns: int
    value: Any


class WriteOut(BaseModel):
    """What a W signal was last set to, after limits, and by whom."""

    value: float | None
    requested: float | None = None
    """What was asked for, when the clamp changed it."""
    at_limit: Limit | None = None
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
    """What a gauge or axis spans: the signal's own, else its limits, else the unit's scale."""
    precision: int | None = None
    warning: tuple[float, float] | None = None
    alarm: tuple[float, float] | None = None
    poll_s: float | None = None
    limits: tuple[float, float] | None = None
    """The effective limits now; a limit that follows another signal is that signal's value."""
    role: str
    """`demand`, `output`, `setting` or `config`."""
    tags: dict[str, str]
    """Groupings across the tree, `{axis: name}`: `{"line": "dry"}`; empty without any."""
    initial: Any = None
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
            range=signal.range,
            precision=spec.precision,
            warning=spec.warning,
            alarm=spec.alarm,
            poll_s=signal.poll_s,
            limits=signal.limits,
            role=spec.role.value,
            tags=spec.tags,
            initial=spec.initial,
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
    commit: bool = False
    mode: Any = None
    """What the device's `mode` becomes when this runs, if it has one."""
    interrupts: bool = False
    demand_of: str | None = None
    """A synthesised `set_<name>`: the demand's path."""
    links: dict[str, str] = {}
    """Argument name -> the path of the demand it is a value for."""

    @classmethod
    def of(cls, spec: CommandSpec) -> CommandOut:
        return cls(
            name=spec.name,
            description=spec.doc,
            simulation=spec.simulation,
            commit=spec.commit,
            mode=spec.mode,
            interrupts=spec.interrupts,
            demand_of=spec.demand_of,
            links={n: p.link for n, p in spec.params.items() if p.link is not None},
        )


class InputOut(BaseModel):
    """An input the device declares: what it is, and what the rig bound to it."""

    name: str
    label: str
    quantity: str
    unit: str
    bound: str | None = None
    """The address of the signal (or namespace) bound to this role, if any."""

    @classmethod
    def of(cls, device: Device, role: str, spec: Input) -> InputOut:
        bound = device.bound.get(role)
        return cls(
            name=spec.name,
            label=spec.label,
            quantity=spec.quantity.name,
            unit=spec.quantity.unit.symbol,
            bound=None if bound is None else bound.address,
        )


class RunOut(BaseModel):
    """How the runtime is polling a device; absent when nothing on it is polled."""

    period_s: float | None
    running: bool
    last_read_ns: int | None
    read_s: float | None = None
    """How long the last read took (the driver's `read` alone), in seconds of rig time."""
    missed: int = 0
    """Reads that took longer than the period since polling began."""
    reading_since_ns: int | None = None
    """When the read in flight began, on the rig's clock; null: none is."""
    consecutive_failures: int = 0
    """Reads in a row that raised; 0 after one that succeeds."""
    next_retry_ns: int | None = None
    """While offline and retrying: when the next read is due, on the rig's clock."""

    @classmethod
    def of(cls, run: DeviceRun) -> RunOut:
        return cls(
            period_s=run.period_s,
            running=run.running,
            last_read_ns=run.last_read_ns,
            read_s=run.read_s,
            missed=run.missed,
            reading_since_ns=run.reading_since_ns,
            consecutive_failures=run.consecutive_failures,
            next_retry_ns=run.next_retry_ns,
        )


class DeviceOut(BaseModel):
    """One entry of `GET /api/devices`: the tree with live values, commands, state, conditions.

    `driver` is the rig file's `driver:` for it, or null for a device built in
    code; `class_name` its Python class; `kind` what claimed its name -- `device`, or
    `simulation` for an application's own simulation device, which a UI
    keeps on its simulation page. `conditions` is what the rig's condition
    store holds on the device and its signals: the runtime's (`offline`,
    `slow`, `write_failed`, ...) and its driver's own.
    """

    name: str
    label: str | None = None
    kind: str
    driver: str | None = None
    class_name: str
    link: str | None = None
    poll_s: float | None = None
    signals: list[SignalOut | NamespaceOut]
    commands: list[CommandOut]
    inputs: dict[str, InputOut]
    """What the device follows, by role: the declared input and the address bound to it."""
    readable: bool
    writable: bool
    conditions: list[Condition]
    """What the rig holds on the device and its signals now, the driver's own included."""
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
        conditions: list[Condition] | None = None,
    ) -> DeviceOut:
        return cls(
            name=device.name,
            label=device.label,
            kind=kind,
            driver=type(device.config).type_name,
            class_name=type(device).__name__,
            link=link,
            poll_s=device.poll_s,
            signals=tree_out(device.root, latest, device.written),
            commands=[CommandOut.of(spec) for spec in device.commands.values()],
            inputs={
                role: InputOut.of(device, role, spec) for role, spec in type(device).INPUTS.items()
            },
            readable=device.readable,
            writable=device.writable,
            conditions=list(conditions or ()),
            run=None if run is None else RunOut.of(run),
        )


# endregion

# region Controllers


class GeneratorOut(BaseModel):
    """A running trajectory as `ControllerOut.reference` shows it: `{type, **its config}`.

    Loosely typed (`extra="allow"`) rather than a union over every
    registered generator, so the shape stays put as generators are added.
    Once started a generator also shows where it lands (`end_time`, in
    seconds from the rig's start; absent while endless); a `profile` shows
    its `segments` as given and `active`, the index of the one in force.
    """

    model_config = ConfigDict(extra="allow")

    type: str

    @classmethod
    def of(cls, generator: SetpointGenerator) -> GeneratorOut:
        return cls.model_validate(generator.wire())


def finite(value: Any) -> Any:
    """`value` with every NaN and infinity made None, through dicts, lists and tuples.

    JSON has no NaN: Python's encoder writes a bare `NaN` that `JSON.parse`
    refuses, and Starlette's HTTP encoder refuses it outright (a 500). A law
    gone wrong, a reading off its range: they cross as `null`.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list | tuple):
        return [finite(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


class ControllerOut(BaseModel):
    """A controller as a client sees it. Separate from `ControllerView` so the wire stays stable.

    Named by `output_signal`, the demand it drives; `measured_signal` is the
    published signal it regulates. `measured` is that signal's last reading
    and `setpoint` is in its unit; `output`, `expected` and `correction` are
    in `output_unit`, the output signal's.
    """

    name: str
    label: str | None = None
    """The output signal's display name; None: show `name`."""
    output_signal: str
    measured_signal: str
    default: bool
    mode: str
    law: SerializeAsAny[ControlLawView] | None
    feedforward: SerializeAsAny[FeedforwardConfig]
    """What maps the setpoint to the output; the law's correction is added to it."""
    output_unit: str
    reference: float | GeneratorOut | None
    setpoint: float | None
    """The reference as resolved at the last tick, so a ramp's current value is on the wire."""
    arrived: bool
    """Whether the reference has landed: a number has; a trajectory once it finishes."""
    correction: float
    output: float | None
    expected: float | None
    delivered_correction: float | None
    measured: ReadingOut | None

    @model_serializer(mode="wrap")
    def _finite(self, handler: SerializerFunctionWrapHandler):
        """Every non-finite float -- the law's state and the reading's too -- as None.

        No return annotation: with one, pydantic would publish it as the
        response schema in place of the model's.
        """
        return finite(handler(self))

    @classmethod
    def of(
        cls, controller: Controller, default: bool, state: ControllerState | None = None
    ) -> ControllerOut:
        """From the controller now, or from `state` (a tick's snapshot) joined to its settings."""
        view = controller.view if state is None else ControllerView.of(controller.spec, state)
        reference = view.reference
        return cls(
            name=view.name,
            label=controller.output_signal.label or None,
            output_signal=controller.output_signal.address,
            measured_signal=controller.measured_signal.address,
            default=default,
            mode=view.mode.value,
            law=view.law,
            feedforward=view.feedforward,
            output_unit=view.output_unit or controller.output_signal.unit.symbol,
            reference=reference
            if isinstance(reference, float | int | type(None))
            else GeneratorOut.of(reference),
            setpoint=view.setpoint,
            arrived=view.arrived,
            correction=view.correction,
            output=view.output,
            expected=view.expected,
            delivered_correction=view.delivered_correction,
            measured=None if view.measured is None else ReadingOut.of(view.measured),
        )


# endregion
