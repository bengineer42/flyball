"""Simulated devices, declarable in a rig file.

A `sim_plant` under `links` is one plant model shared by the devices that
use it: a `sim_daq` reads chosen plant outputs as its signals and advances
the plant to the read's instant -- once, however many devices read it; a
`sim_drive` sets chosen plant inputs from demands on its signals. Several of
each may share one plant, so a multi-zone plant's zones interact through it
(`examples/furnace`'s worked scenario, or an application's own
[MultiPlant][flyball_sim.plant.MultiPlant] such as
[humctrl](https://github.com/bengineer42/humctrl)'s chamber). A rig of these
runs on a laptop, ticks like a real one, records, tunes and serves the same
API -- with nothing plugged in -- and, laid over a real rig's file, stands
in for its hardware under the same names.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Mapping
from typing import Any, Literal

from flyball.foundation.config import Config, resolve
from flyball.foundation.device import (
    Access,
    Band,
    Committable,
    Condition,
    DriverConfig,
    Level,
    Node,
    NodeSpec,
    Readable,
    Role,
    Sample,
    Signal,
    SignalSpec,
    command,
)
from flyball.foundation.errors import HardwareError, NotFoundError
from flyball.foundation.quantities import DIMENSIONLESS, Quantity
from flyball.foundation.quantities.si import Celsius, Watt
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .plant import Fopdt, Integrator, Lag, MultiPlant, Noisy, Plant, advancer

# A plant's drive is a fraction of full power: 0 is off, 1 is everything it has.
Drive = DIMENSIONLESS.unit("fraction of full drive", "of full")

TEMPERATURE_C = Quantity("temperature", Celsius)
POWER_W = Quantity("power", Watt)
DRIVE = Quantity("drive", Drive)

OUTPUT = "output"
"""The one output port of a bare `sim_plant`."""
INPUT = "input"
"""The one input port of a bare `sim_plant`."""

# region The plants


class PlantConfig(Config[Plant], tag="sim_plant"):
    """A plant model with one input and one output, shared by the devices that use it.

    Its ports are `input` (a drive, 0 to 1 of full) and `output`, whose
    quantity the `sim_daq` reading it says, since a lag may be an oven or a
    pressure vessel. An unknown key is refused, so the old `kind` does not
    silently mean the default model.
    """

    model_config = ConfigDict(extra="forbid")

    model: Literal["lag", "integrator", "fopdt"] = "lag"
    tau_s: float = Field(default=10.0, gt=0, description="Time constant (lag, fopdt).")
    dead_s: float = Field(default=0.0, ge=0, description="Dead time (fopdt).")
    gain: float = 1.0
    leak: float = Field(default=0.0, ge=0, description="Drain rate (integrator).")
    ambient: float = Field(
        default=0.0,
        description="Where a lag rests with no input (lag, fopdt).",
        json_schema_extra={"live": "output"},
    )
    initial: float = Field(default=0.0, json_schema_extra={"live": "output"})
    noise: float = Field(
        default=0.0,
        ge=0,
        description="Gaussian noise on what is read, in the output's unit.",
        json_schema_extra={"live": "stats.noise"},
    )
    seed: int | None = None

    def build(self) -> Plant:
        """Always wrapped in [Noisy][flyball.sim.plant.Noisy], so noise can be turned on live."""
        plant: Plant
        match self.model:
            case "lag":
                plant = Lag(self.tau_s, self.initial, self.gain, ambient=self.ambient)
            case "integrator":
                plant = Integrator(self.gain, self.leak, self.initial)
            case "fopdt":
                plant = Fopdt(
                    self.tau_s, self.dead_s, self.gain, self.initial, ambient=self.ambient
                )
        return Noisy(plant, self.noise, self.seed)

    def retune(self, plant: Plant) -> None:
        """Apply this config's parameters to a plant already built from one of the same model.

        The state (output, input) is untouched: a simulation keeps running
        through the change, as a real plant would. `model` cannot change.

        Raises:
            ValueError: If `plant` is not of this config's model.
        """
        noisy = plant if isinstance(plant, Noisy) else None
        inner: Any = noisy.plant if noisy is not None else plant
        if noisy is not None:
            noisy.sigma = self.noise
        match self.model, inner:
            case "lag", Lag():
                inner.tau_s, inner.gain, inner.ambient = self.tau_s, self.gain, self.ambient
            case "integrator", Integrator():
                inner.gain, inner.leak = self.gain, self.leak
            case "fopdt", Fopdt():
                inner.dead_s = self.dead_s
                lag = inner._lag
                lag.tau_s, lag.gain, lag.ambient = self.tau_s, self.gain, self.ambient
            case _:
                raise ValueError(f"plant is a {type(inner).__name__}, not a {self.model}")


type AnyPlant = Plant | MultiPlant
type PlantLink = PlantConfig | str
"""A `sim_daq`/`sim_drive`'s `link`: a `sim_plant` config, or -- the ordinary rig-file case,
several devices sharing one plant -- the name of a link declared once and built separately
(`resolve()` builds a `Config`, passes anything else through unchanged, so an application's
own `MultiPlant` config such as `examples/furnace`'s `FurnaceConfig`, or an already-built
plant object, both work here too even though this narrower type is what a bare `sim_plant`
validates against inline)."""


def _plant(link: Any) -> AnyPlant:
    """The plant behind a device's link: built from a config, or the shared object itself."""
    if isinstance(link, str):
        raise TypeError(f"link {link!r} must be resolved to a plant before building")
    plant = resolve(link)
    if not isinstance(plant, (MultiPlant, Plant)):
        raise TypeError(f"link is a {type(plant).__name__}, not a simulated plant")
    return plant


# A plant is one of three shapes: a `MultiPlant` that knows its own ports'
# quantities -- optional `output_quantity(port)`/`input_quantity(port)` hooks,
# duck-typed rather than on the `MultiPlant` protocol itself so an ordinary
# `MultiPlant` need not implement them (`examples/furnace`'s `Furnace` is the
# worked example: temperatures out, a power in per zone); any other
# `MultiPlant`, with named ports whose outputs the file must describe and
# whose inputs are a fraction of full; or a bare `Plant`, whose one output
# and one input are `output` and `input`.


def _output_ports(plant: AnyPlant) -> tuple[str, ...]:
    return plant.output_names if isinstance(plant, MultiPlant) else (OUTPUT,)


def _input_ports(plant: AnyPlant) -> tuple[str, ...]:
    return tuple(plant.inputs) if isinstance(plant, MultiPlant) else (INPUT,)


def _output_quantity(plant: AnyPlant, port: str) -> Quantity | None:
    """What a plant's output port measures, if the plant knows -- a furnace's are temperatures."""
    if port not in _output_ports(plant):
        raise ValueError(f"no output port {port!r}; there are {_output_ports(plant)}")
    hook = getattr(plant, "output_quantity", None)
    return hook(port) if hook is not None else None


def _input_quantity(plant: AnyPlant, port: str) -> tuple[Quantity, Band]:
    """What a plant's input port takes and its range: watts for a heater, a fraction otherwise."""
    if port not in _input_ports(plant):
        raise ValueError(f"no input port {port!r}; there are {_input_ports(plant)}")
    hook = getattr(plant, "input_quantity", None)
    known = hook(port) if hook is not None else None
    return known if known is not None else (DRIVE, (0.0, 1.0))


def _read_output(plant: AnyPlant, port: str) -> float:
    return plant.output(port) if isinstance(plant, MultiPlant) else plant.output


def _set_drive(plant: AnyPlant, port: str, fraction: float) -> None:
    """Put `fraction` of full drive on the plant's `port`."""
    if isinstance(plant, MultiPlant):
        plant.inputs[port] = fraction
    else:
        plant.input = fraction


def _get_input(plant: AnyPlant, port: str) -> float:
    """The plant's drive on `port`, as a fraction of full."""
    return plant.inputs[port] if isinstance(plant, MultiPlant) else plant.input


def _feedforward(plant: AnyPlant, port: str, demand: float) -> float:
    """The drive on `port` that would hold `demand` at steady state: the plant's static inverse."""
    if isinstance(plant, MultiPlant):
        return plant.feedforward(port, demand)
    return plant.feedforward(demand)


def _static_inverse(plant: AnyPlant, port: str) -> Callable[[float], float] | None:
    """`port`'s own drive -> steady-state-demand map, if the plant has one.

    `Noisy` does not forward `inverse_feedforward` -- it is not part of the
    `Plant` protocol, only a few models' own -- but noise is zero-mean, so
    the clean model it wraps still answers for the static range.
    """
    inner = plant.plant if isinstance(plant, Noisy) else plant
    inverse = getattr(inner, "inverse_feedforward", None)
    if inverse is None:
        return None
    return (lambda drive: inverse(port, drive)) if isinstance(inner, MultiPlant) else inverse


def _static_range(name: str, path: str, plant: AnyPlant, port: str) -> Band:
    """The output-unit span a `demand: output` port can deliver, read off the plant's own model."""
    inverse = _static_inverse(plant, port)
    if inverse is None:
        raise ValueError(
            f"{name}.{path}: the plant has no static range for {port!r}; give `limits`"
        )
    lo, hi = inverse(0.0), inverse(1.0)
    return (min(lo, hi), max(lo, hi))


def _demand_quantity(
    name: str, path: str, plant: AnyPlant, port: str, quantity: str | None, unit: str | None
) -> Quantity:
    """What a `demand: output` port measures: a furnace zone's temperature, or spelled out."""
    hook = getattr(plant, "output_quantity", None)
    known = hook(port) if hook is not None else None
    if quantity is not None or unit is not None:
        if quantity is None or unit is None:
            raise ValueError(f"{name}.{path}: say both `quantity` and `unit`, or neither")
        return Quantity(quantity, unit)
    if known is None:
        raise ValueError(
            f"{name}.{path}: the plant has no quantity of its own; say `{{quantity, unit}}`"
        )
    return known


def _tree(device: str, leaves: Mapping[str, SignalSpec]) -> tuple[NodeSpec | SignalSpec, ...]:
    """The tree for signals keyed by dotted path: one atomic namespace per path prefix.

    `{"dry.humidity": …, "dry.temperature": …, "wet.humidity": …}` becomes
    namespaces `dry` and `wet`, each read whole -- one plant advance yields
    everything at one instant -- so a sim overlay can mirror a namespaced
    real device address for address. A flat key is a leaf on
    the root, as before.

    Raises:
        ValueError: A path is empty, has an empty segment, or names both a
            namespace and a signal.
    """
    branches: dict[str, Any] = {}  # a nested dict per namespace, a SignalSpec per leaf
    for path, spec in leaves.items():
        segments = path.split(".")
        if not path or "" in segments:
            raise ValueError(f"{device}: {path!r} is not a signal path")
        node: dict[str, Any] = branches
        for segment in segments[:-1]:
            child = node.setdefault(segment, {})
            if not isinstance(child, dict):
                raise ValueError(f"{device}.{segment}: both a namespace and a signal")
            node = child
        if segments[-1] in node:
            raise ValueError(f"{device}.{path}: both a namespace and a signal")
        node[segments[-1]] = spec

    def specs(branch: Mapping[str, Any]) -> tuple[NodeSpec | SignalSpec, ...]:
        return tuple(
            NodeSpec(name=name, atomic=True, children=specs(child))
            if isinstance(child, dict)
            else child
            for name, child in branch.items()
        )

    return specs(branches)


# endregion
# region sim_daq


class DaqPort(BaseModel):
    """A `sim_daq` signal spelled out: which port, and what it measures if the plant cannot say."""

    model_config = ConfigDict(extra="forbid")

    port: str
    quantity: str | None = Field(
        default=None, description="What the output is: 'temperature', 'level'."
    )
    unit: str | None = None
    label: str | None = Field(default=None, description="Display text: 'Dry line humidity'.")
    tags: dict[str, str] | None = Field(
        default=None, description="Groupings across the tree, `{axis: name}`: `{line: dry}`."
    )
    range: Band | None = None
    precision: int | None = None
    warn: Band | None = Field(
        default=None, description="The band a value is normal inside; outside it, a warning."
    )
    alarm: Band | None = Field(
        default=None, description="The band a value is acceptable inside; outside it, an alarm."
    )


class SimDaq(Readable):
    """Reads a plant's outputs as its signals, advancing the plant to each read's instant.

    A plant is stepped once per instant however many devices read it, so two
    `sim_daq`s on one bare `sim_plant` do not run its time at twice the rate.

    Every signal is an `[RP]` output. Each is read when its own `poll_s` is
    due, so a slow sample thermocouple beside fast zone ones costs one
    device; what is due at an instant goes out as one sample. A sensor
    failed by `fail` is a condition until `restore`.
    """

    def __init__(
        self,
        name: str,
        plant: AnyPlant,
        ports: Mapping[str, str | DaqPort],
        label: str | None = None,
        config: SimDaqConfig | None = None,
    ) -> None:
        super().__init__(name, label)
        self.plant = plant
        self._config = config
        leaves: dict[str, SignalSpec] = {}
        self.ports: dict[str, str] = {}
        """The plant port behind each signal, by the signal's path (`"dry.humidity"`)."""
        for path, spec in ports.items():
            spec = DaqPort(port=spec) if isinstance(spec, str) else spec
            quantity = _output_quantity(plant, spec.port)
            if spec.quantity is not None or spec.unit is not None:
                if spec.quantity is None or spec.unit is None:
                    raise ValueError(f"{name}.{path}: say both `quantity` and `unit`, or neither")
                quantity = Quantity(spec.quantity, spec.unit)
            if quantity is None:
                raise ValueError(
                    f"{name}.{path}: the plant's {spec.port!r} has no quantity of its own;"
                    " say `{port, quantity, unit}`"
                )
            leaves[path] = SignalSpec(
                name=path.rpartition(".")[2],
                quantity=quantity,
                access=Access.RP,
                label=spec.label or "",
                tags=dict(spec.tags or {}),
                range=spec.range,
                precision=spec.precision,
                warn=spec.warn,
                alarm=spec.alarm,
            )
            self.ports[path] = spec.port
        if not leaves:
            raise ValueError(f"{name}: a sim_daq reads at least one port")
        self.bind(_tree(name, leaves))
        self._advancer = None if isinstance(plant, MultiPlant) else advancer(plant)
        """A bare plant's stepper, shared with every other device reading the same plant."""
        self._last_ns: int | None = None
        self._last_read: dict[Signal, int] = {}
        self._broken: dict[Signal, int] = {}

    @property
    def config(self) -> SimDaqConfig:
        """The config this was built from, or one describing it when built in code.

        `link` is `""`: the plant was bound in place, and only the rig file
        names it.
        """
        if self._config is not None:
            return self._config.model_copy(update={"link": ""})
        return SimDaqConfig(link="", ports=dict(self.ports))

    @property
    def broken(self) -> tuple[str, ...]:
        """Signals failed by `fail`, until `restore`."""
        return tuple(str(s.path) for s in self._broken)

    def _push_conditions(self) -> None:
        self.conditions.push(
            tuple(
                Condition("broken", Level.ERROR, f"{s.path}: sensor failed (simulated)", since)
                for s, since in self._broken.items()
            )
        )

    def _advance(self, time_ns: int) -> None:
        """Step the plant to `time_ns`: once per instant, whichever device reading it asks first."""
        if isinstance(self.plant, MultiPlant):
            self.plant.advance(time_ns)
        elif self._advancer is not None:
            self._advancer.advance(time_ns)
        self._last_ns = time_ns

    def _due(self, signal: Signal, time_ns: int) -> bool:
        """Whether `signal`'s own period has passed since it was last read.

        A tenth of the period short still counts: a 2 s signal on a device
        polled every second is read on the second poll, not the third, when
        a scaled clock's threads arrive a little early.
        """
        if (period := signal.poll_s) is None or (last := self._last_read.get(signal)) is None:
            return True
        return time_ns - last >= 0.9 * period * 1e9

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """The poll (no `node`) reads what is due by its own period; a node asked for is read whole.

        A fresh read names the node it wants and expects a value now, so
        the per-signal gate applies only to the runtime's own polling. One
        sample per namespace read (each is atomic) and one for the leaves
        on the node itself, all at the one instant the plant was advanced
        to.
        """
        asked = node is not None
        node = self.root if node is None else node
        signals = [self.signals[path] for path in self.ports if node.contains(self.signals[path])]
        if broken := [s.path for s in signals if s in self._broken]:
            raise HardwareError(f"{self.name}.{broken[0]}: sensor failed (simulated)")
        self._advance(time_ns)
        by_node: dict[Node, dict[Signal, float]] = {}
        for signal in signals:
            if asked or self._due(signal, time_ns):
                value = _read_output(self.plant, self.ports[str(signal.path)])
                by_node.setdefault(signal.node, {})[signal] = value
                self._last_read[signal] = time_ns
        for read_node, values in by_node.items():
            yield Sample(read_node, time_ns, values)

    def _signal(self, name: str) -> Signal:
        try:
            return self.signals[name]
        except KeyError:
            raise NotFoundError(f"{self.name} has no signal {name!r}") from None

    @command(simulation=True)
    def fail(self, signal: str) -> tuple[str, ...]:
        """Break one sensor: reads raise until `restore`; what the controller does is the test."""
        self._broken.setdefault(self._signal(signal), self._last_ns or 0)
        self._push_conditions()
        return self.broken

    @command(simulation=True)
    def restore(self, signal: str) -> tuple[str, ...]:
        """Mend the sensor; a command on an offline device makes the rig poll it again."""
        self._broken.pop(self._signal(signal), None)
        self._push_conditions()
        return self.broken


class SimDaqConfig(DriverConfig[SimDaq], tag="sim_daq"):
    """Read chosen outputs of a simulated plant as this device's `[RP]` signals."""

    link: PlantLink = Field(  # pyright: ignore[reportIncompatibleVariableOverride]
        description="The plant link read: `sim_plant`, or another package's own `MultiPlant`"
        " link, such as `examples/furnace`'s `sim_furnace`."
    )
    ports: dict[str, str | DaqPort] = Field(
        description="Signal path -> the plant's output port; spelled out with `quantity` and"
        " `unit` when the plant does not say what a port measures (a bare `sim_plant`)."
        " A dotted path (`dry.humidity`) puts the signal in a namespace, so the device"
        " can mirror a real one's addresses."
    )

    def build(self, name: str, label: str | None = None) -> SimDaq:
        return SimDaq(name, _plant(self.link), self.ports, label, self)


SimDaq.config_type = SimDaqConfig  # the config is declared after the device it builds


# endregion
# region sim_drive


class DrivePort(BaseModel):
    """A `sim_drive` signal spelled out: which port, and the unit and limits it is set in.

    In the default `demand: input` form the signal is declared in that unit
    with those limits, so an overlay can mirror a real device's writable
    signal unit for unit (`blender.humidity [W] %RH 0..100`); a demand is
    mapped linearly over `limits` onto the port's 0..1 drive.

    `demand: output` instead declares the signal in the plant's own output
    unit -- what a `sim_daq` reading the same plant would read -- and
    `commit` inverts the plant's static model to find the drive, so a
    controller can hand it the setpoint directly (the `setpoint`
    feedforward). `quantity`/`unit` may be omitted when the plant knows its
    output (a furnace zone); `limits` may be omitted when the plant's model
    has a static inverse (`Lag`, `Fopdt`; not `Integrator`) -- otherwise
    both must be given.
    """

    model_config = ConfigDict(extra="forbid")

    port: str
    demand: Literal["input", "output"] = "input"
    label: str | None = Field(default=None, description="Display text: 'Zone 1 heater'.")
    tags: dict[str, str] | None = Field(
        default=None, description="Groupings across the tree, `{axis: name}`: `{zone: 1}`."
    )
    quantity: str | None = Field(
        default=None,
        description="What is set: 'humidity', 'power' -- or, in output mode, what the plant's"
        " output measures, if it cannot say.",
    )
    unit: str | None = None
    limits: Band | None = Field(
        default=None,
        description="Input mode: what maps onto the port's drive, `limits[0]` off, `limits[1]`"
        " full. Output mode: the deliverable span, defaulted from the plant's model if it has one.",
    )

    @model_validator(mode="after")
    def _limits_span(self) -> DrivePort:
        if self.limits is not None and self.limits[1] <= self.limits[0]:
            raise ValueError("limits must be a rising span")
        return self


class SimDrive(Committable):
    """Drives a plant's inputs from demands on its signals.

    Every signal is a demand in the port's own unit -- watts for a furnace
    heater, a fraction of full for a bare plant -- or, spelled out as a
    [DrivePort][flyball.sim.devices.DrivePort], in whatever unit the real
    device it stands in for takes: `demand: input` (the default) maps a
    demand linearly over `limits` onto the port's 0..1 drive, knowing
    nothing of the plant's dynamics -- a controller's feedforward does. A
    port declared `demand: output` instead takes a demand in the plant's
    own output unit and inverts the plant's static model to find the
    drive, so the smart work moves from the controller's feedforward into
    the drive itself. Either way the rig clamps a demand to `limits`
    before it gets here.
    """

    def __init__(
        self,
        name: str,
        plant: AnyPlant,
        ports: Mapping[str, str | DrivePort],
        label: str | None = None,
        config: SimDriveConfig | None = None,
    ) -> None:
        super().__init__(name, label)
        self.plant = plant
        self._config = config
        self.ports: dict[str, str] = {}
        """The plant port behind each signal, by the signal's path (`"dry.flow"`)."""
        self._spans: dict[str, Band] = {}
        """What each signal's `limits` were declared as: the span its values map over."""
        self._smart: set[str] = set()
        """Paths declared `demand: output`: `commit` inverts the plant instead of a linear map."""
        self._disturbed: dict[str, tuple[float, int | None]] = {}
        """Path -> (fraction offset, expiry time_ns or None) from `disturb`, re-applied on top
        of every `commit` until re-set with a fresh offset, cleared with `0.0`, or expired."""
        leaves: dict[str, SignalSpec] = {}
        for path, spec in ports.items():
            if isinstance(spec, str):
                quantity, limits = _input_quantity(plant, spec)
                port = spec
            elif spec.demand == "output":
                _input_quantity(plant, spec.port)  # the port exists
                quantity = _demand_quantity(name, path, plant, spec.port, spec.quantity, spec.unit)
                limits = spec.limits
                if limits is None:
                    limits = _static_range(name, path, plant, spec.port)
                port = spec.port
                self._smart.add(path)
            else:
                if spec.quantity is None or spec.unit is None or spec.limits is None:
                    raise ValueError(f"{name}.{path}: say `quantity`, `unit` and `limits`")
                _input_quantity(plant, spec.port)  # the port exists
                quantity, limits, port = Quantity(spec.quantity, spec.unit), spec.limits, spec.port
            spelled = None if isinstance(spec, str) else spec
            leaves[path] = SignalSpec(
                name=path.rpartition(".")[2],
                quantity=quantity,
                access=Access.RPW,
                role=Role.DEMAND,
                label=(spelled and spelled.label) or "",
                tags=dict((spelled and spelled.tags) or {}),
                limits=limits,
            )
            self.ports[path] = port
            self._spans[path] = limits
        if not leaves:
            raise ValueError(f"{name}: a sim_drive drives at least one port")
        self.bind(_tree(name, leaves))

    def _fraction(self, path: str, value: float) -> float:
        """`value` in the signal's unit as a fraction of full drive: linear over its span."""
        low, high = self._spans[path]
        return (value - low) / (high - low)

    @property
    def config(self) -> SimDriveConfig:
        """The config this was built from, or one describing it when built in code."""
        if self._config is not None:
            return self._config.model_copy(update={"link": ""})
        return SimDriveConfig(link="", ports=dict(self.ports))  # the short form, as bound

    @property
    def inputs(self) -> dict[str, float]:
        """What the plant is actually driven with on each signal's port, as a fraction of full."""
        return {name: _get_input(self.plant, port) for name, port in self.ports.items()}

    def _disturbance(self, path: str, time_ns: int) -> float:
        """The offset still in force on `path` at `time_ns`; expired ones are forgotten."""
        entry = self._disturbed.get(path)
        if entry is None:
            return 0.0
        offset, expiry = entry
        if expiry is not None and time_ns >= expiry:
            del self._disturbed[path]
            return 0.0
        return offset

    def commit(self, time_ns: int) -> None:
        for signal, value in self.pending.items():
            path = str(signal.path)
            port = self.ports[path]
            fraction = (
                min(1.0, max(0.0, _feedforward(self.plant, port, value)))
                if path in self._smart
                else self._fraction(path, value)
            )
            _set_drive(self.plant, port, fraction + self._disturbance(path, time_ns))

    @command(simulation=True)
    def disturb(
        self, signal: str, offset: float, duration_s: float | None = None
    ) -> dict[str, float]:
        """Kick the plant's drive on `signal`'s port by `offset` in the signal's unit.

        A door opened, a leak: the plant sees it, the controller does not
        until the reading moves. `offset` is watts on a furnace heater, a
        fraction of full on a bare plant, the declared unit on a spelled-out
        port. The kick persists across later commits -- a regulated loop's
        own demand no longer wipes it out -- until re-set with a fresh
        `disturb`, cleared with `offset=0.0`, or, with `duration_s` given,
        it expires on its own. `offset` must be finite.
        """
        try:
            port = self.ports[signal]
        except KeyError:
            raise NotFoundError(f"{self.name} has no signal {signal!r}") from None
        if not math.isfinite(offset):
            raise ValueError(f"{self.name}.{signal}: disturb offset {offset!r}: must be finite")
        low, high = self._spans[signal]
        fraction_offset = offset / (high - low)
        now_ns = self.router.now_ns()
        base = _get_input(self.plant, port) - self._disturbance(signal, now_ns)
        _set_drive(self.plant, port, base + fraction_offset)
        expiry = None if duration_s is None else now_ns + round(duration_s * 1e9)
        self._disturbed[signal] = (fraction_offset, expiry)
        return self.inputs


class SimDriveConfig(DriverConfig[SimDrive], tag="sim_drive"):
    """Drive chosen inputs of a simulated plant from this device's `[W]` signals."""

    link: PlantLink = Field(  # pyright: ignore[reportIncompatibleVariableOverride]
        description="The plant link driven: `sim_plant`, or another package's own `MultiPlant`"
        " link, such as `examples/furnace`'s `sim_furnace`."
    )
    ports: dict[str, str | DrivePort] = Field(
        description="Signal path -> the plant's input port, or spelled out with the `quantity`,"
        " `unit` and `limits` the signal is set in (mapped linearly onto the port's 0..1 drive),"
        " or `demand: output` to declare it in the plant's own output unit and let `commit` invert"
        " the plant. A dotted path is a namespace."
    )

    def build(self, name: str, label: str | None = None) -> SimDrive:
        return SimDrive(name, _plant(self.link), self.ports, label, self)


SimDrive.config_type = SimDriveConfig


# endregion
