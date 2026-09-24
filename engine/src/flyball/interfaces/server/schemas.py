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
from collections.abc import Callable, Mapping
from typing import Any, Literal

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
    InputBinding,
    Limit,
    Node,
    NoValue,
    Quality,
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
from flyball.rig.bands import on_no_value

# Every built-in law, direct: no extension defines one today
# (`control/configs.py` registers all of them), and building this from
# `get_catalog()`/`Catalogs.discover()` at import time would risk
# `discover()` re-entering a module still mid-import through some
# extension's own import chain -- see `runtime/config.py`'s equivalent
# comment on `LawConfig`/`FeedforwardConfig` there.
_LAWS = (OpenLoop, P, PI, PID, IMC, OnOff, SmithPredictor, Scheduled, SlidingMode)
LawConfig = discriminated_union(
    {law.type: law for law in _LAWS}, "type", lambda law: law.config_type
)
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


def value_out(value: Any) -> Any:
    """A reading's value on the wire: `null` for a no-value, else the value as it is."""
    return None if isinstance(value, NoValue) else value


def caveats_out(signal: Signal, value: Any, at_limit: Limit | None) -> dict[str, Any] | None:
    """The caveats on a usable value, or None: `at_limit` (railed, or clamped), `out_of_range`.

    `out_of_range` is `low` or `high` when the value lies outside the signal's own `range`
    (the values it can plausibly take), as the driver or the rig file declares it.
    """
    caveats: dict[str, Any] = {}
    if at_limit is not None:
        caveats["at_limit"] = at_limit.value
    band = signal.spec.range
    if band is not None and isinstance(value, int | float) and not isinstance(value, bool):
        if value < band[0]:
            caveats["out_of_range"] = "low"
        elif value > band[1]:
            caveats["out_of_range"] = "high"
    return caveats or None


def _without_none(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """`data` without those of `keys` whose value is None: optional detail, left out unless set."""
    return {k: v for k, v in data.items() if not (k in keys and v is None)}


_DETAIL = ("reason", "caveats", "last_usable", "age_s")


class LatestOut(BaseModel):
    """The last reading on a signal, without repeating its address.

    `value` is null when the reading has none; `quality` says why (`invalid`, `stale`,
    `not_applicable`), `reason` what the driver or the rig gave. A usable value may carry
    `caveats`. `reason` and `caveats` are left out when there are none.
    """

    time_ns: int
    value: Any
    quality: Quality = Quality.OK
    reason: str | None = None
    caveats: dict[str, Any] | None = None

    @model_serializer(mode="wrap")
    def _sparse(self, handler: SerializerFunctionWrapHandler):
        """`reason` and `caveats` only when set; `value` always, null or not."""
        return _without_none(handler(self), _DETAIL)

    @classmethod
    def of(cls, reading: Reading) -> LatestOut:
        return cls(
            time_ns=reading.time_ns,
            value=value_out(reading.value),
            quality=reading.quality,
            reason=reading.reason or None,
            caveats=caveats_out(reading.signal, reading.value, reading.at_limit)
            if reading.usable
            else None,
        )


class ReadingOut(BaseModel):
    """One value on one signal at one instant.

    With no value: `value` null, `quality` and `reason` say why, `last_usable` is the newest
    reading that had one, and `age_s` how long ago that was, on the rig's clock: from when the
    rig received it (`received_ns`), else when it was read.
    """

    signal: str
    time_ns: int
    value: Any
    quality: Quality = Quality.OK
    reason: str | None = None
    caveats: dict[str, Any] | None = None
    last_usable: LatestOut | None = None
    age_s: float | None = None

    @model_serializer(mode="wrap")
    def _sparse(self, handler: SerializerFunctionWrapHandler):
        """The detail only when set; `value` always, null or not. Non-finite floats as null."""
        return finite(_without_none(handler(self), _DETAIL))

    @classmethod
    def of(
        cls, reading: Reading, last_usable: Reading | None = None, now_ns: int | None = None
    ) -> ReadingOut:
        latest = LatestOut.of(reading)
        usable = None if reading.usable or last_usable is None else last_usable
        return cls(
            signal=reading.signal.address,
            time_ns=reading.time_ns,
            value=latest.value,
            quality=latest.quality,
            reason=latest.reason,
            caveats=latest.caveats,
            last_usable=None if usable is None else LatestOut.of(usable),
            age_s=None
            if usable is None or now_ns is None
            else max(0.0, (now_ns - (usable.received_ns or usable.time_ns)) / 1e9),
        )


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

    A value is `null` when the reading has none: `quality` says why and `reason` what was
    given, for those only (sparse, keyed the same way; absent means `ok`). `caveats` are
    those of the usable values that carry any (`at_limit`, `out_of_range`), likewise.
    `writes` carries the write record (`requested`, `at_limit`, `controller`) for each demand
    the sample includes, keyed the same way as `values` -- `/ws/writes` folded in here.
    """

    node: str
    time_ns: int
    values: dict[str, Any]
    quality: dict[str, Quality] = {}
    reason: dict[str, str] = {}
    caveats: dict[str, dict[str, Any]] = {}
    writes: dict[str, WriteMetaOut] = {}

    @model_serializer(mode="wrap")
    def _sparse(self, handler: SerializerFunctionWrapHandler):
        """`quality`, `reason` and `caveats` only when something in the sample has them."""
        return {
            k: v
            for k, v in handler(self).items()
            if not (k in ("quality", "reason", "caveats") and not v)
        }

    @classmethod
    def of(cls, sample: Sample, latest: Mapping[Signal, Reading] | None = None) -> SampleOut:
        writes: dict[str, WriteMetaOut] = {}
        values: dict[str, Any] = {}
        quality: dict[str, Quality] = {}
        reason: dict[str, str] = {}
        caveats: dict[str, dict[str, Any]] = {}
        node = sample.node
        for signal, value in sample.values.items():
            name = node.relative(signal)
            if isinstance(value, NoValue):
                values[name] = None
                quality[name] = value.quality
                if value.reason:
                    reason[name] = value.reason
            else:
                values[name] = value
                if (marked := caveats_out(signal, value, sample.marks.get(signal))) is not None:
                    caveats[name] = marked
            if (
                latest is not None
                and signal.role is Role.DEMAND
                and (reading := latest.get(signal)) is not None
            ):
                writes[name] = WriteMetaOut(
                    requested=reading.requested,
                    at_limit=reading.at_limit,
                    controller=reading.controller,
                )
        return cls(
            node=node.address,
            time_ns=sample.time_ns,
            values=values,
            quality=quality,
            reason=reason,
            caveats=caveats,
            writes=writes,
        )


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
    committed state of a writable signal. `quality` is `pending` before the
    first reading, else the newest reading's; with no value, `last_usable`
    is the newest reading that had one. `readback` is a demand's (`echo`,
    `sensed`); `on_no_value` a banded signal's, as in force. `stale_after_s` is the threshold
    the rig judges it by now -- its own, or `max(3·poll_s, 5 s)` while its device is polled --
    or null: it is not judged (a push, a setting, an echo demand). Past it with nothing
    arriving, the rig pushes `stale` on it.
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
    stale_after_s: float | None = None
    limits: tuple[float, float] | None = None
    """The effective limits now; a limit that follows another signal is that signal's value."""
    role: str
    """`demand`, `output`, `setting` or `config`."""
    tags: dict[str, str]
    """Groupings across the tree, `{axis: name}`: `{"line": "dry"}`; empty without any."""
    initial: Any = None
    quality: Quality = Quality.PENDING
    readback: str | None = None
    """A demand's: `echo` (its reading is what was committed) or `sensed`."""
    on_no_value: str | None = None
    """A banded signal's: `fire` or `ignore`, the default resolved."""
    latest: LatestOut | None = None
    last_usable: LatestOut | None = None
    """With no value now: the newest reading that had one."""
    write: WriteOut | None = None

    @classmethod
    def of(
        cls,
        signal: Signal,
        latest: Reading | None,
        write: WriteState | None,
        last_usable: Reading | None = None,
        stale_after_s: float | None = None,
    ) -> SignalOut:
        spec = signal.spec
        banded = spec.warning is not None or spec.alarm is not None
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
            stale_after_s=stale_after_s,
            limits=signal.limits,
            role=spec.role.value,
            tags=spec.tags,
            initial=spec.initial,
            quality=Quality.PENDING if latest is None else latest.quality,
            readback=spec.readback.value if spec.role is Role.DEMAND else None,
            on_no_value=on_no_value(signal).value if banded else None,
            latest=None if latest is None else LatestOut.of(latest),
            last_usable=None
            if latest is None or latest.usable or last_usable is None
            else LatestOut.of(last_usable),
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
    node: Node,
    latest: dict[Signal, Reading],
    written: dict[Signal, WriteState],
    last_usable: Mapping[Signal, Reading] | None = None,
    stale_after: Callable[[Signal], float | None] | None = None,
) -> list[SignalOut | NamespaceOut]:
    """The signals and namespaces directly under `node`, recursing into the namespaces.

    `stale_after`: each signal's liveness threshold now (the rig's `liveness.threshold_s`).
    """
    usable = last_usable or {}
    out: list[SignalOut | NamespaceOut] = [
        SignalOut.of(
            signal,
            latest.get(signal),
            written.get(signal),
            usable.get(signal),
            None if stale_after is None else stale_after(signal),
        )
        for signal in node.signals.values()
    ]
    out.extend(
        NamespaceOut(
            name=child.name,
            address=child.address,
            atomic=child.atomic,
            label=child.label,
            poll_s=child.poll_s,
            signals=tree_out(child, latest, written, last_usable, stale_after),
        )
        for child in node.children.values()
    )
    return out


class CommandOut(BaseModel):
    name: str
    description: str | None = None
    simulation: bool = False
    commit: bool = False
    sets_mode: Any = None
    """What the device's `mode` becomes when this runs, if it has one."""
    interrupts: bool = False
    writes: list[str] = []
    """What it moves that no linked argument says (`CommandSpec.writes`): demand paths, or a
    private child's name."""
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
            sets_mode=spec.sets_mode,
            interrupts=spec.interrupts,
            writes=list(spec.writes),
            demand_of=spec.demand_of,
            links={n: p.link for n, p in spec.params.items() if p.link is not None},
        )


class InterruptedOut(BaseModel):
    """A controller a command put into manual once it had succeeded."""

    controller: str
    was: str
    """Its mode before: `regulating`."""


class CommandRunOut(BaseModel):
    """A command's response: what the method returned, and the controllers it put in manual."""

    result: Any = None
    """Whatever the command's method returned (null for most)."""
    interrupted: list[InterruptedOut] = []
    """Each controller an `interrupts` command put into manual; empty when it displaced none."""


class InputOut(BaseModel):
    """One input of a device: what it follows (an address, or a number) and its quality now.

    Every input the device has: those its driver declares, and any other name the rig file's
    `inputs:` gives. `bound` is the source's address, `constant` the number for an input bound
    to one; `quality`/`reason`/`age_s` are the source's now, as a reading of it shows them
    (a number is always `ok`).
    """

    name: str
    label: str
    """The declared input's label; `""` for a name only the rig file gives."""
    quantity: str
    """The declared input's quantity, else the source signal's; `""` when neither says."""
    unit: str
    """The source signal's unit, else the declared input's; `""` when neither says."""
    bound: str | None = None
    """The address of the signal (or namespace) it follows; null for a number, or unbound."""
    constant: float | None = None
    """The number, for an input bound to one (`inputs: {dry: 36.5}`)."""
    quality: Quality = Quality.PENDING
    """`ok`; `pending` before the source's first reading (or while unbound); else the
    source's no-value quality (`stale`, `invalid`, `not_applicable`)."""
    reason: str | None = None
    """The source's reason for having no value, when it gives one."""
    age_s: float | None = None
    """Seconds since the rig received the source's newest reading with a value."""

    @model_serializer(mode="wrap")
    def _sparse(self, handler: SerializerFunctionWrapHandler):
        """`constant`, `reason` and `age_s` only when set."""
        return _without_none(handler(self), ("constant", "reason", "age_s"))

    @classmethod
    def of(cls, binding: InputBinding, now_ns: int | None = None) -> InputOut:
        declared = binding.declared
        source = binding.signal
        unit = binding.unit
        return cls(
            name=binding.name,
            label="" if declared is None else declared.label,
            quantity=declared.quantity.name
            if declared is not None
            else ("" if source is None else source.quantity.name),
            unit="" if unit is None else unit.symbol,
            bound=binding.address,
            constant=binding.constant,
            quality=binding.quality,
            reason=binding.reason or None,
            age_s=binding.age_s(now_ns),
        )


class ValueSourceOut(BaseModel):
    """Where a `driver: values` signal's value in force came from, for the device page.

    `rig_file`: its `initial`; `restored`: kept from an earlier run ("restored, written by
    `writer` at `written_ns`"); `written`: written in this run.
    """

    origin: Literal["rig_file", "restored", "written"]
    initial: Any
    """The rig file's `initial` in force."""
    writer: str | None = None
    written_ns: int | None = None
    """Wall time, ns since the epoch."""


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
    """Each input by name: what it follows (an address or a number) and its quality now."""
    consumers: dict[str, list[str]] = {}
    """Who follows each signal of this device, by path: the inputs bound to it
    (`blender.inputs.dry`), or to a namespace above it; a signal nobody follows is left out."""
    sources: dict[str, ValueSourceOut] = {}
    """A `driver: values` device's: where each value in force came from, by path."""
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
        last_usable: Mapping[Signal, Reading] | None = None,
        stale_after: Callable[[Signal], float | None] | None = None,
        consumers: Callable[[Signal], list[InputBinding]] | None = None,
        sources: Callable[[Signal], Any] | None = None,
        now_ns: int | None = None,
    ) -> DeviceOut:
        return cls(
            name=device.name,
            label=device.label,
            kind=kind,
            driver=type(device.config).type_name,
            class_name=type(device).__name__,
            link=link,
            poll_s=device.poll_s,
            signals=tree_out(device.root, latest, device.written, last_usable, stale_after),
            commands=[CommandOut.of(spec) for spec in device.commands.values()],
            inputs={name: InputOut.of(b, now_ns) for name, b in device.bound.items()},
            consumers={
                path: [b.where for b in found]
                for path, signal in device.signals.items()
                if consumers is not None and (found := consumers(signal))
            },
            sources={
                path: ValueSourceOut(
                    origin=source.origin,
                    initial=source.initial,
                    writer=source.writer,
                    written_ns=source.written_ns,
                )
                for path, signal in device.signals.items()
                if sources is not None and (source := sources(signal)) is not None
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
    is_default: bool
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
    output_value: float | None
    expected: float | None
    delivered_correction: float | None
    measured_value: ReadingOut | None
    on_fault: str | dict[str, Any] = "freeze"
    """What it does once its source has been faulty for its wait: `freeze`, `manual`, `stop`,
    `stop_device`, or `{freeze_s, then}`."""
    latched: list[str] = []
    """The causes of every latch that refuses its `regulate` now (`stop`, `on_fault:<name>`)."""

    @model_serializer(mode="wrap")
    def _finite(self, handler: SerializerFunctionWrapHandler):
        """Every non-finite float -- the law's state and the reading's too -- as None.

        No return annotation: with one, pydantic would publish it as the
        response schema in place of the model's.
        """
        return finite(handler(self))

    @classmethod
    def of(
        cls,
        controller: Controller,
        is_default: bool,
        state: ControllerState | None = None,
        latched: list[str] | None = None,
    ) -> ControllerOut:
        """From the controller now, or from `state` (a tick's snapshot) joined to its settings.

        `latched`: the causes of the latches that refuse its `regulate`, where the caller
        knows them (the rig's routes); none on a tick's snapshot.
        """
        view = controller.view if state is None else ControllerView.of(controller.spec, state)
        reference = view.reference
        return cls(
            name=view.name,
            label=controller.output_signal.label or None,
            output_signal=controller.output_signal.address,
            measured_signal=controller.measured_signal.address,
            is_default=is_default,
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
            output_value=view.output_value,
            expected=view.expected,
            delivered_correction=view.delivered_correction,
            measured_value=None
            if view.measured_value is None
            else ReadingOut.of(view.measured_value),
            on_fault=controller.on_fault.document(),
            latched=list(latched or []),
        )


# endregion
