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
from typing import Literal

from pydantic import Field

from flyball.core.config import Config, resolve
from flyball.core.device import DeviceConfig, DeviceSettings, DeviceState, command
from flyball.core.reading import Measurand, Reader, Sample, Source
from flyball.core.sink import Actuator, ActuatorState
from flyball.core.units.dimension import Unit

from .plant import Fopdt, Integrator, Lag, Noisy, Plant


class PlantConfig(Config[Plant], tag="sim_plant"):
    """A plant model, built once and shared between its reader and actuator."""

    kind: Literal["lag", "integrator", "fopdt"] = "lag"
    tau_s: float = Field(default=10.0, gt=0, description="Time constant (lag, fopdt).")
    dead_s: float = Field(default=0.0, ge=0, description="Dead time (fopdt).")
    gain: float = 1.0
    leak: float = Field(default=0.0, ge=0, description="Drain rate (integrator).")
    ambient: float = Field(default=0.0, description="Where a lag rests with no input (lag, fopdt).")
    initial: float = 0.0
    noise: float = Field(
        default=0.0, ge=0, description="Gaussian noise on what is read, in the output's unit."
    )
    seed: int | None = None

    def build(self) -> Plant:
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
        return Noisy(plant, self.noise, self.seed) if self.noise else plant


@dataclass(frozen=True, slots=True, kw_only=True)
class SimReaderState(DeviceState):
    output: float | None = None
    input: float = 0.0


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

    @property
    def state(self) -> SimReaderState:
        return SimReaderState(output=self._output, input=self.plant.input)

    def read(self, time_ns: int) -> Iterable[Sample]:
        if self._last_ns is not None and time_ns > self._last_ns:  # a reset clock: no step
            self.plant.step((time_ns - self._last_ns) / 1e9)
        self._last_ns = time_ns
        self._output = self.plant.output
        return [
            Sample(self.source, self.source.next_seq(), time_ns, {self.measurand: self._output})
        ]


class SimReaderConfig(DeviceConfig[SimReader], tag="sim_reader"):
    name: str
    link: PlantConfig | str
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
            resolve(self.link),
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
    input: float = 0.0
    """What the plant is actually being driven with: the demand, clamped."""


class SimActuator(Actuator):
    """Drives a plant's input from the loop's demand.

    The demand is in the *output's* unit -- "hold 50 °C" -- as every loop's
    is; the actuator turns it into an input through the plant's static
    feedforward and clamps to its limits, and the loop's correction covers
    what the feedforward gets wrong. That is the split every real actuator
    makes (the humidity blender's flows for a target %RH); here it is exact
    by construction, which is why `disturb` exists.
    """

    def __init__(
        self,
        name: str,
        plant: Plant,
        limits: tuple[float, float] = (0.0, 1.0),
        unit: str | None = None,
    ) -> None:
        super().__init__(name)
        self.plant = plant
        self.limits = limits
        self._demand: float | None = None
        if unit:
            self.demand_unit = Unit.get(unit)  # type: ignore[misc]

    @property
    def settings(self) -> SimActuatorSettings:
        return SimActuatorSettings(limits=self.limits)

    @property
    def state(self) -> SimActuatorState:
        return SimActuatorState(demand=self._demand, input=self.plant.input)

    def set_demand(self, demand: float) -> float | None:
        self._demand = demand
        lo, hi = self.limits
        self.plant.input = min(hi, max(lo, self.plant.feedforward(demand)))
        return None  # what the plant will do is the reader's to report

    @command
    def set_limits(self, low: float, high: float) -> SimActuatorSettings:
        """Change what the actuator can deliver: a smaller heater, a stuck valve."""
        if high <= low:
            raise ValueError("high must exceed low")
        self.limits = (low, high)
        return self.settings

    @command
    def disturb(self, offset: float) -> SimActuatorState:
        """Kick the plant's input by `offset` until the next demand: a door opened, a leak."""
        self.plant.input += offset
        return self.state


class SimActuatorConfig(DeviceConfig[SimActuator], tag="sim_actuator"):
    name: str
    link: PlantConfig | str
    limits: tuple[float, float] = (0.0, 1.0)
    unit: str | None = None

    def build(self) -> SimActuator:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a plant before building")
        return SimActuator(self.name, resolve(self.link), self.limits, self.unit)
