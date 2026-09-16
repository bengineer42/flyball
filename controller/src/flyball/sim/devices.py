"""Simulated devices, declarable in a rig file.

A `sim_plant` under `links` is one plant model shared by a `sim_reader`, which
reads its output as a measurand and advances it by the time since the last
read, and a `sim_actuator`, which sets its input from the loop's demand. A
rig of these runs on a laptop, ticks like a real one, records, tunes and
serves the same API -- with nothing plugged in.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field

from flyball.core.config import Config, resolve
from flyball.core.device import (
    Condition,
    DeviceConfig,
    DeviceSettings,
    DeviceState,
    Level,
    command,
)
from flyball.core.errors import HardwareError
from flyball.core.reading import Measurand, Reader, Sample, Source
from flyball.core.sink import Actuator, ActuatorState
from flyball.core.units import DIMENSIONLESS, Quantity
from flyball.core.units.dimension import Unit
from flyball.core.units.dimensions import Power

from .furnace import Furnace, MultiPlant, Port
from .plant import Fopdt, Integrator, Lag, Noisy, Plant

# A plant's drive is a fraction of full power: 0 is off, 1 is everything it has.
Drive = DIMENSIONLESS.unit("fraction of full drive", "of full")


class PlantConfig(Config[Plant], tag="sim_plant"):
    """A plant model, built once and shared between its reader and actuator."""

    kind: Literal["lag", "integrator", "fopdt"] = "lag"
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
        match self.kind:
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
        """Apply this config's parameters to a plant already built from one of the same kind.

        The state (output, input) is untouched: a simulation keeps running
        through the change, as a real plant would. `kind` cannot change.

        Raises:
            ValueError: If `plant` is not of this config's kind.
        """
        noisy = plant if isinstance(plant, Noisy) else None
        inner: Any = noisy.plant if noisy is not None else plant
        if noisy is not None:
            noisy.sigma = self.noise
        match self.kind, inner:
            case "lag", Lag():
                inner.tau_s, inner.gain, inner.ambient = self.tau_s, self.gain, self.ambient
            case "integrator", Integrator():
                inner.gain, inner.leak = self.gain, self.leak
            case "fopdt", Fopdt():
                inner.dead_s = self.dead_s
                lag = inner._lag
                lag.tau_s, lag.gain, lag.ambient = self.tau_s, self.gain, self.ambient
            case _:
                raise ValueError(f"plant is a {type(inner).__name__}, not a {self.kind}")


class FurnaceConfig(Config[Furnace], tag="sim_furnace"):
    """A multi-zone furnace ([Furnace][flyball.sim.furnace.Furnace]).

    Ports: inputs `heaterN`, outputs `zoneN` and `sample`.
    """

    zones: int = Field(default=3, ge=1)
    power_w: float | list[float] = 2000.0
    capacity_j_per_k: float | list[float] = 5000.0
    coupling_w_per_k: float = Field(default=5.0, ge=0)
    loss_w_per_k: float = Field(default=2.0, ge=0)
    emissivity: float = Field(default=0.8, ge=0, le=1)
    area_m2: float = Field(default=0.02, ge=0)
    ambient_c: float = Field(default=20.0, json_schema_extra={"live": "outputs.*"})
    sample_capacity_j_per_k: float = Field(default=800.0, gt=0)
    sample_coupling_w_per_k: float = Field(default=4.0, ge=0)
    sample_zone: int = Field(default=2, ge=1)
    sensor_lag_s: float = Field(default=3.0, ge=0)
    noise: float = Field(default=0.0, ge=0, json_schema_extra={"live": "stats.noise"})
    seed: int | None = None
    initial_c: float | None = Field(default=None, json_schema_extra={"live": "outputs.*"})

    def build(self) -> Furnace:
        return Furnace(**self.model_dump(exclude={"tag"}))

    def retune(self, plant: Any) -> None:
        """Apply the parameters to a running furnace; its temperatures stay where they are."""
        if not isinstance(plant, Furnace) or plant.zones != self.zones:
            raise ValueError("the number of zones cannot change while it runs")
        fresh = self.build()
        for attr in (
            "power",
            "capacity",
            "coupling",
            "loss",
            "emissivity",
            "area",
            "ambient",
            "sample_capacity",
            "sample_coupling",
            "sample_zone",
            "sensor_lag_s",
            "noise",
        ):
            setattr(plant, attr, getattr(fresh, attr))


def _port(link: Any, port: str | None, *, output: bool) -> Any:
    """The plant a device reads or drives: single-port as is, multi-port through `port`."""
    plant = resolve(link)
    if isinstance(plant, MultiPlant):
        if port is None:
            raise ValueError(f"a {type(plant).__name__} has several ports; say which with `port`")
        return Port(plant, output=port) if output else Port(plant, input=port)
    if port is not None:
        raise ValueError(f"a {type(plant).__name__} has one port; drop `port`")
    return plant


@dataclass(frozen=True, slots=True, kw_only=True)
class SimReaderState(DeviceState):
    output: float | None = None
    input: Quantity(Drive, ge=0, le=1) | None = 0.0  # type: ignore[valid-type]
    """The plant's input; None for one port of a multi-port plant, which has many."""


class SimReader(Reader):
    """Reads a plant's output as one measurand, advancing it by the time since the last read."""

    def __init__(
        self,
        name: str,
        plant: Plant,
        measurand: str,
        unit: str,
        range: tuple[float, float] | None = None,
        precision: int | None = None,
        warn: tuple[float, float] | None = None,
        alarm: tuple[float, float] | None = None,
    ) -> None:
        self.plant = plant
        self.measurand = Measurand(
            measurand, Unit.get(unit), range=range, precision=precision, warn=warn, alarm=alarm
        )
        self.source = Source(name, (self.measurand,))
        super().__init__(name, (self.source,))
        self._last_ns: int | None = None
        self._output: float | None = None
        self._broken = False
        self._broken_ns = 0

    @property
    def state(self) -> SimReaderState:
        return SimReaderState(
            output=self._output,
            input=self.plant.input if not isinstance(self.plant, Port) else None,
            conditions=(
                (Condition("broken", Level.ERROR, "sensor failed (simulated)", self._broken_ns),)
                if self._broken
                else ()
            ),
        )

    def read(self, time_ns: int) -> Iterable[Sample]:
        if self._broken:
            raise HardwareError(f"{self.name}: thermocouple open circuit (simulated)")
        if isinstance(self.plant, Port):
            self.plant.advance(time_ns)  # once per instant, however many ports are read
        elif self._last_ns is not None and time_ns > self._last_ns:  # a reset clock: no step
            self.plant.step((time_ns - self._last_ns) / 1e9)
        self._last_ns = time_ns
        self._output = self.plant.output
        return [
            Sample(self.source, self.source.next_seq(), time_ns, {self.measurand: self._output})
        ]

    @command(simulation=True)
    def fail(self) -> SimReaderState:
        """Break the sensor: every read raises until `restore`. What the loop does is the test."""
        self._broken = True
        self._broken_ns = self._last_ns or 0
        return self.state

    @command(simulation=True)
    def restore(self) -> SimReaderState:
        """Mend the sensor; a command on an offline reader makes the rig poll it again."""
        self._broken = False
        return self.state


class SimReaderConfig(DeviceConfig[SimReader], tag="sim_reader"):
    name: str
    link: PlantConfig | FurnaceConfig | str
    port: str | None = Field(default=None, description="Which output of a multi-port plant.")
    measurand: str = Field(
        description="The measurand the plant's output is: 'temperature', 'level'."
    )
    unit: str
    range: tuple[float, float] | None = None
    precision: int | None = None
    warn: tuple[float, float] | None = Field(
        default=None, description="The band a value is normal inside; outside it, a warning."
    )
    alarm: tuple[float, float] | None = Field(
        default=None, description="The band a value is acceptable inside; outside it, an alarm."
    )

    def build(self) -> SimReader:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a plant before building")
        return SimReader(
            self.name,
            _port(self.link, self.port, output=True),
            self.measurand,
            self.unit,
            self.range,
            self.precision,
            warn=self.warn,
            alarm=self.alarm,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SimActuatorSettings(DeviceSettings):
    limits: tuple[float, float] = (0.0, 1.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class SimActuatorState(ActuatorState):
    input: Quantity(Drive, ge=0, le=1) = 0.0  # type: ignore[valid-type]
    """What the plant is actually being driven with: the demand, clamped."""


class SimActuator(Actuator):
    """Drives a plant's input from the loop's demand.

    Two kinds of actuator, told apart by `unit`:

    - A *smart* one takes the demand in the output's unit -- "hold 50 °C" --
      and turns it into an input through the plant's static feedforward,
      clamped to its limits; the loop's correction covers what the
      feedforward gets wrong. That is what a packaged controller does, and
      here it is exact by construction, which is why `disturb` exists. This
      is the default, and any unit that is not a drive.
    - A *plain* one takes the drive itself: a fraction of full (`of full`)
      or a power (`W`, with `power_w` saying what full is). The loop's
      feedforward, not the actuator, knows the plant.
    """

    def __init__(
        self,
        name: str,
        plant: Plant,
        limits: tuple[float, float] = (0.0, 1.0),
        unit: str | None = None,
        config: SimActuatorConfig | None = None,
        power_w: float | None = None,
    ) -> None:
        super().__init__(name)
        self.plant = plant
        self.limits = limits
        self._demand: float | None = None
        self._config = config
        if unit:
            self.demand_unit = Unit.get(unit)  # type: ignore[misc]
        # In drive units a demand is the plant's input, scaled: the actuator does no modelling.
        dimension = None if self.demand_unit is None else self.demand_unit.dimension
        self.scale: float | None = None
        if dimension == DIMENSIONLESS:
            self.scale = 1.0
        elif dimension == Power:
            if power_w is None:
                raise ValueError(f"{name}: a demand in {unit} needs `power_w`, what full drive is")
            self.scale = power_w * self.demand_unit.factor  # type: ignore[union-attr]

    @property
    def config(self) -> SimActuatorConfig:
        """The config this was built from, or one describing it when built in code.

        `link` is `""`: the plant was bound in place, and only the rig file
        (`/api/sim/config`) names it.
        """
        if self._config is not None:
            return self._config.model_copy(update={"link": ""})
        return SimActuatorConfig(name=self.name, link="", limits=self.limits)

    @property
    def settings(self) -> SimActuatorSettings:
        return SimActuatorSettings(limits=self.limits)

    @property
    def state(self) -> SimActuatorState:
        return SimActuatorState(demand=self._demand, input=self.plant.input)

    def set_demand(self, demand: float) -> float | None:
        """Drive the plant's input for `demand`; report the demand actually deliverable.

        When the drive is clamped to the limits, the returned value is the
        demand the clamped drive stands for, if the plant can say -- what a
        law's anti-windup tracks. `None` means "no better than the demand".
        """
        self._demand = demand
        lo, hi = self.limits
        if self.scale is not None:
            drive = min(hi, max(lo, demand / self.scale))
            self.plant.input = drive
            return drive * self.scale
        wanted = self.plant.feedforward(demand)
        drive = min(hi, max(lo, wanted))
        self.plant.input = drive
        if drive == wanted:
            return demand
        inverse = getattr(self.plant, "inverse_feedforward", None)
        return inverse(drive) if inverse is not None else None

    @command(simulation=True)
    def set_limits(self, low: float, high: float) -> SimActuatorSettings:
        """Change what the actuator can deliver: a smaller heater, a stuck valve."""
        if high <= low:
            raise ValueError("high must exceed low")
        self.limits = (low, high)
        return self.settings

    @command(simulation=True)
    def disturb(self, offset: float) -> SimActuatorState:
        """Kick the plant's input by `offset` until the next demand: a door opened, a leak."""
        self.plant.input += offset
        return self.state


class SimActuatorConfig(DeviceConfig[SimActuator], tag="sim_actuator"):
    name: str
    link: PlantConfig | FurnaceConfig | str
    port: str | None = Field(default=None, description="Which input of a multi-port plant.")
    limits: tuple[float, float] = Field(
        default=(0.0, 1.0),
        description="What the drive is clamped to.",
        json_schema_extra={"live": "state.input"},
    )
    unit: str | None = Field(
        default=None,
        description="What demands arrive in: the output's unit (the actuator models the plant),"
        " `of full` (the drive itself) or a power (`W`, with `power_w`).",
    )
    power_w: float | None = Field(
        default=None, gt=0, description="What full drive is, when demands are a power."
    )

    def build(self) -> SimActuator:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a plant before building")
        return SimActuator(
            self.name,
            _port(self.link, self.port, output=False),
            self.limits,
            self.unit,
            self,
            self.power_w,
        )


# The actuator is defined before its config, so `config`'s return annotation
# could not be resolved at class creation; the schema route reads this.
SimActuator.config_type = SimActuatorConfig
