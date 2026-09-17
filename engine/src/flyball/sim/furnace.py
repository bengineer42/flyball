"""A multi-zone tube furnace: heated zones in a row, a sample inside, thermocouples that lag.

The complicated example. Each zone is a thermal mass with its own heater,
losing heat to ambient by convection and by radiation -- so the plant's gain
falls as it gets hot, and a tuning found at 200 °C is wrong at 900 --
and exchanging heat with its neighbours, so the zones' loops interact. The
sample sits in one zone with its own mass and lags behind it; the
thermocouples lag behind everything.

Inputs are `heater1..N` (0 to 1 of each zone's power); outputs are
`zone1..N` and `sample`, as the thermocouples read them.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from threading import Lock
from typing import Protocol, runtime_checkable

STEFAN_BOLTZMANN = 5.670374419e-8
KELVIN = 273.15


@runtime_checkable
class MultiPlant(Protocol):
    """A plant with named inputs and outputs, stepped once per instant however many read it."""

    @property
    def inputs(self) -> dict[str, float]: ...

    @property
    def output_names(self) -> tuple[str, ...]: ...

    def output(self, port: str) -> float: ...

    def advance(self, time_ns: int) -> None:
        """Step to `time_ns`; a second call with the same instant does nothing."""
        ...

    def feedforward(self, port: str, demand: float) -> float: ...

    def inverse_feedforward(self, port: str, drive: float) -> float: ...


class Port:
    """One output (and optionally one input) of a multi-port plant, seen as a single-port plant."""

    __slots__ = ("input_name", "output_name", "plant")

    def __init__(
        self, plant: MultiPlant, output: str | None = None, input: str | None = None
    ) -> None:
        if output is not None and output not in plant.output_names:
            raise ValueError(f"no output {output!r}; there are {plant.output_names}")
        if input is not None and input not in plant.inputs:
            raise ValueError(f"no input {input!r}; there are {tuple(plant.inputs)}")
        self.plant = plant
        self.output_name = output
        self.input_name = input

    @property
    def input(self) -> float:
        if self.input_name is None:
            raise AttributeError("this port has no input")
        return self.plant.inputs[self.input_name]

    @input.setter
    def input(self, value: float) -> None:
        if self.input_name is None:
            raise AttributeError("this port has no input")
        self.plant.inputs[self.input_name] = value

    @property
    def output(self) -> float:
        if self.output_name is None:
            raise AttributeError("this port has no output")
        return self.plant.output(self.output_name)

    def advance(self, time_ns: int) -> None:
        self.plant.advance(time_ns)

    def step(self, dt_s: float) -> float:
        raise TypeError("a multi-port plant is stepped by time, not by interval: use advance()")

    def feedforward(self, demand: float) -> float:
        if self.input_name is None:
            raise AttributeError("this port has no input")
        return self.plant.feedforward(self.input_name, demand)

    def inverse_feedforward(self, drive: float) -> float:
        if self.input_name is None:
            raise AttributeError("this port has no input")
        return self.plant.inverse_feedforward(self.input_name, drive)


class Furnace:
    """N heated zones in a row, a sample in one of them, thermocouples that lag.

    All temperatures in °C. Per zone, with `u` the heater fraction:

        C dT/dt = P u - h (T - Ta) - e s A ((T+273)^4 - (Ta+273)^4)
                  + k (T_left - T) + k (T_right - T) - ks (T - T_sample)

    Args:
        zones: How many.
        power_w: Each zone's heater at full drive.
        capacity_j_per_k: Each zone's thermal mass.
        coupling_w_per_k: Conduction between neighbouring zones.
        loss_w_per_k: Convective loss from each zone to ambient.
        emissivity: For the radiative loss, with `area_m2` per zone.
        area_m2: Radiating area per zone.
        ambient_c: Where everything rests.
        sample_capacity_j_per_k: The sample's thermal mass.
        sample_coupling_w_per_k: Between the sample and its zone.
        sample_zone: Which zone holds the sample, from 1.
        sensor_lag_s: Each thermocouple's first-order lag.
        noise: Gaussian noise on every reading, in °C.
        initial_c: Where everything starts.
        max_step_s: Integration sub-step; smaller is slower and more exact.
    """

    def __init__(
        self,
        zones: int = 3,
        power_w: float | Sequence[float] = 2000.0,
        capacity_j_per_k: float | Sequence[float] = 5000.0,
        coupling_w_per_k: float = 5.0,
        loss_w_per_k: float = 2.0,
        emissivity: float = 0.8,
        area_m2: float = 0.02,
        ambient_c: float = 20.0,
        sample_capacity_j_per_k: float = 800.0,
        sample_coupling_w_per_k: float = 4.0,
        sample_zone: int = 2,
        sensor_lag_s: float = 3.0,
        noise: float = 0.0,
        seed: int | None = None,
        initial_c: float | None = None,
        max_step_s: float = 1.0,
    ) -> None:
        if zones < 1:
            raise ValueError("at least one zone")
        self.zones = zones
        self.power = _per_zone(power_w, zones, "power_w")
        self.capacity = _per_zone(capacity_j_per_k, zones, "capacity_j_per_k")
        self.coupling = coupling_w_per_k
        self.loss = loss_w_per_k
        self.emissivity = emissivity
        self.area = area_m2
        self.ambient = ambient_c
        self.sample_capacity = sample_capacity_j_per_k
        self.sample_coupling = sample_coupling_w_per_k
        if not 1 <= sample_zone <= zones:
            raise ValueError(f"sample_zone must be 1..{zones}")
        self.sample_zone = sample_zone
        self.sensor_lag_s = sensor_lag_s
        self.noise = noise
        self.max_step_s = max_step_s
        self._random = random.Random(seed)
        start = ambient_c if initial_c is None else initial_c
        self.temperature = [start] * zones
        self.sample = start
        self.measured = [start] * zones
        self.sample_measured = start
        self._inputs = {f"heater{i + 1}": 0.0 for i in range(zones)}
        self._last_ns: int | None = None
        self._lock = Lock()

    # region MultiPlant

    @property
    def inputs(self) -> dict[str, float]:
        return self._inputs

    @property
    def output_names(self) -> tuple[str, ...]:
        return (*(f"zone{i + 1}" for i in range(self.zones)), "sample")

    def output(self, port: str) -> float:
        clean = (
            self.sample_measured
            if port == "sample"
            else self.measured[_zone_index(port, self.zones)]
        )
        return clean + (self._random.gauss(0.0, self.noise) if self.noise else 0.0)

    def advance(self, time_ns: int) -> None:
        # Readers poll on their own threads outside the rig lock; only one may integrate.
        with self._lock:
            if self._last_ns is not None and time_ns > self._last_ns:
                self.step((time_ns - self._last_ns) / 1e9)
            if self._last_ns is None or time_ns > self._last_ns:
                self._last_ns = time_ns

    def feedforward(self, port: str, demand: float) -> float:
        """The drive that holds `demand` in this zone alone, at steady state: losses over power.

        A heater cannot cool, so a demand below ambient comes back *negative*:
        the actuator clamps it to zero and reports the temperature zero drive
        holds (ambient), which is what a law's anti-windup needs to see.
        Clamping here instead would make a sub-ambient demand look fully
        deliverable and let the integral wind up without limit.
        """
        i = _zone_index(port.replace("heater", "zone"), self.zones)
        return self._losses(demand) / self.power[i] if self.power[i] else 0.0

    def inverse_feedforward(self, port: str, drive: float) -> float:
        """The temperature `drive` holds in this zone alone: the losses curve, inverted."""
        i = _zone_index(port.replace("heater", "zone"), self.zones)
        wanted = self.power[i] * min(1.0, max(0.0, drive))
        low, high = self.ambient, 3000.0
        for _ in range(60):
            mid = (low + high) / 2
            if self._losses(mid) < wanted:
                low = mid
            else:
                high = mid
        return (low + high) / 2

    # endregion

    def _losses(self, temperature: float) -> float:
        """Heat leaving a zone at `temperature`; negative below ambient, and monotonic.

        The radiative term is floored at 0 K: a demand below -273 °C (a law
        wound far negative with no anti-windup) must not turn the fourth
        power positive again and switch the heater on.
        """
        kelvin = max(temperature + KELVIN, 0.0)
        radiative = (
            self.emissivity
            * STEFAN_BOLTZMANN
            * self.area
            * (kelvin**4 - (self.ambient + KELVIN) ** 4)
        )
        return self.loss * (temperature - self.ambient) + radiative

    def step(self, dt_s: float) -> None:
        """Integrate forward by `dt_s`, in sub-steps of at most `max_step_s`."""
        remaining = dt_s
        while remaining > 0:
            h = min(remaining, self.max_step_s)
            self._euler(h)
            remaining -= h

    def _euler(self, h: float) -> None:
        temps = self.temperature
        n = self.zones
        flows = []
        for i in range(n):
            drive = min(1.0, max(0.0, self._inputs[f"heater{i + 1}"]))
            q = self.power[i] * drive - self._losses(temps[i])
            if i > 0:
                q += self.coupling * (temps[i - 1] - temps[i])
            if i < n - 1:
                q += self.coupling * (temps[i + 1] - temps[i])
            if i == self.sample_zone - 1:
                q -= self.sample_coupling * (temps[i] - self.sample)
            flows.append(q)
        zone_of_sample = temps[self.sample_zone - 1]
        self.sample += (
            h * self.sample_coupling * (zone_of_sample - self.sample) / self.sample_capacity
        )
        for i in range(n):
            temps[i] += h * flows[i] / self.capacity[i]
        # Thermocouples lag: first order towards what they sit in.
        if self.sensor_lag_s > 0:
            a = min(1.0, h / self.sensor_lag_s)
            for i in range(n):
                self.measured[i] += a * (temps[i] - self.measured[i])
            self.sample_measured += a * (self.sample - self.sample_measured)
        else:
            self.measured = list(temps)
            self.sample_measured = self.sample

    def reset(self, temperature: float) -> None:
        """Everything at `temperature`, heaters off, thermocouples agreeing."""
        self.temperature = [temperature] * self.zones
        self.measured = [temperature] * self.zones
        self.sample = self.sample_measured = temperature
        for name in self._inputs:
            self._inputs[name] = 0.0


def _per_zone(value: float | Sequence[float], zones: int, name: str) -> list[float]:
    if isinstance(value, (int, float)):
        return [float(value)] * zones
    if len(value) != zones:
        raise ValueError(f"{name} needs one value per zone ({zones}), got {len(value)}")
    return [float(v) for v in value]


def _zone_index(port: str, zones: int) -> int:
    if port.startswith("zone") and port[4:].isdigit() and 1 <= int(port[4:]) <= zones:
        return int(port[4:]) - 1
    raise ValueError(f"no zone {port!r}; there are zone1..zone{zones}")


__all__ = ["Furnace", "MultiPlant", "Port"]
