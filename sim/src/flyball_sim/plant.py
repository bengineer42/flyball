"""Plants: what a loop pushes on, and how it pushes back.

Each is a small model with an `input`, an `output`, and `advance(dt_s)`, held
exactly between steps so the step size does not change the trajectory. A
`Plant` is what a simulated reader reads and a simulated actuator drives;
one plant object is shared between them, the way one chamber is.

[MultiPlant][flyball_sim.plant.MultiPlant] is the same idea with several
named inputs and outputs, stepped once per instant however many devices
read it -- a multi-zone furnace, a chamber with several sensed lines. Any
object shaped this way will do, not only the worked furnace example
(`examples/furnace`); a rig's own `sim.py` may define one, the way
[humctrl](https://github.com/bengineer42/humctrl)'s `HumidityChamber` does.
"""

from __future__ import annotations

import random
import threading
import weakref
from collections import deque
from math import exp
from typing import Protocol, runtime_checkable


@runtime_checkable
class Plant(Protocol):
    @property
    def input(self) -> float: ...
    @input.setter
    def input(self, value: float) -> None: ...

    @property
    def output(self) -> float: ...

    def advance(self, dt_s: float) -> float: ...

    def feedforward(self, demand: float) -> float:
        """The input that would hold `demand` at steady state, if the model knows one."""
        ...


class Lag:
    """A first-order lag: `dy/dt = (ambient + gain * u - y) / tau`.

    A heater in a room, a stirred tank's temperature: with no input the
    output settles at `ambient`; full input adds `gain`.
    """

    __slots__ = ("__weakref__", "ambient", "gain", "input", "tau_s", "value")

    def __init__(
        self,
        tau_s: float,
        value: float = 0.0,
        gain: float = 1.0,
        input: float = 0.0,
        ambient: float = 0.0,
    ) -> None:
        if tau_s <= 0:
            raise ValueError("time constant must be positive")
        self.tau_s = tau_s
        self.value = value
        self.gain = gain
        self.input = input
        self.ambient = ambient

    @property
    def output(self) -> float:
        return self.value

    def feedforward(self, demand: float) -> float:
        return (demand - self.ambient) / self.gain if self.gain else 0.0

    def inverse_feedforward(self, drive: float) -> float:
        """The output `drive` holds at steady state: what a clamped actuator can deliver."""
        return self.ambient + self.gain * drive

    def drive(self, u: float, dt_s: float) -> float:
        """Set the input to `u` and hold it for `dt_s` seconds; return the new output."""
        self.input = u
        return self.advance(dt_s)

    def advance(self, dt_s: float) -> float:
        """Hold the input for `dt_s` seconds; return the new output."""
        target = self.ambient + self.gain * self.input
        self.value = target + (self.value - target) * exp(-dt_s / self.tau_s)
        return self.value


class Integrator:
    """`dy/dt = gain * u - leak * y`: a tank filling against a drain, a position under velocity."""

    __slots__ = ("__weakref__", "gain", "input", "leak", "value")

    def __init__(
        self, gain: float = 1.0, leak: float = 0.0, value: float = 0.0, input: float = 0.0
    ) -> None:
        self.gain = gain
        self.leak = leak
        self.value = value
        self.input = input

    @property
    def output(self) -> float:
        return self.value

    def feedforward(self, demand: float) -> float:
        # Holding a level against a drain takes leak * y / gain; a pure
        # integrator holds anything with no input at all.
        return demand * self.leak / self.gain if self.gain else 0.0

    def advance(self, dt_s: float) -> float:
        if self.leak > 0:  # exact for a held input
            steady = self.gain * self.input / self.leak
            self.value = steady + (self.value - steady) * exp(-self.leak * dt_s)
        else:
            self.value += self.gain * self.input * dt_s
        return self.value


class Fopdt:
    """First order plus dead time: a lag whose input arrives `dead_s` late. The autotune case."""

    __slots__ = ("__weakref__", "_lag", "_now", "_pipe", "dead_s", "input")

    def __init__(
        self,
        tau_s: float,
        dead_s: float,
        gain: float = 1.0,
        value: float = 0.0,
        input: float = 0.0,
        ambient: float = 0.0,
    ) -> None:
        if dead_s < 0:
            raise ValueError("dead time cannot be negative")
        self._lag = Lag(tau_s, value, gain, input, ambient)
        self.dead_s = dead_s
        self.input = input
        self._pipe: deque[tuple[float, float]] = deque()  # (arrives_at_s, value)
        self._now = 0.0

    @property
    def output(self) -> float:
        return self._lag.output

    def feedforward(self, demand: float) -> float:
        return self._lag.feedforward(demand)

    def inverse_feedforward(self, drive: float) -> float:
        return self._lag.inverse_feedforward(drive)

    def advance(self, dt_s: float) -> float:
        """Hold the input for `dt_s` seconds; return the new output.

        What went in `dead_s` ago reaches the lag at that instant, not at the
        end of the step: the lag is stepped exactly up to each arrival and on
        from it, so the step size changes nothing.
        """
        self._pipe.append((self._now + self.dead_s, self.input))
        end = self._now + dt_s
        at = self._now
        while self._pipe and self._pipe[0][0] <= end:
            arrives, value = self._pipe.popleft()
            if arrives > at:
                self._lag.advance(arrives - at)
                at = arrives
            self._lag.input = value
        if end > at:
            self._lag.advance(end - at)
        self._now = end
        return self._lag.output


class Noisy:
    """Any plant, read through Gaussian noise. The plant itself stays clean."""

    __slots__ = ("__weakref__", "_random", "plant", "sigma")

    def __init__(self, plant: Plant, sigma: float, seed: int | None = None) -> None:
        self.plant = plant
        self.sigma = sigma
        self._random = random.Random(seed)

    @property
    def input(self) -> float:
        return self.plant.input

    @input.setter
    def input(self, value: float) -> None:
        self.plant.input = value

    @property
    def output(self) -> float:
        return self.plant.output + self._random.gauss(0.0, self.sigma)

    def feedforward(self, demand: float) -> float:
        return self.plant.feedforward(demand)

    def advance(self, dt_s: float) -> float:
        self.plant.advance(dt_s)
        return self.output


class Advancer:
    """Steps one single-port plant to a time, once per instant however many devices read it.

    The bare-[Plant][flyball_sim.plant.Plant] counterpart of
    [MultiPlant.advance_to][flyball_sim.plant.MultiPlant.advance_to]: each reader
    polls on its own thread, so the first to reach an instant steps the plant
    and the rest find it there. An earlier instant than the last does
    nothing. Get the one shared by every reader of a plant from
    [advancer][flyball_sim.plant.advancer].
    """

    __slots__ = ("_last_ns", "_lock", "plant")

    def __init__(self, plant: Plant) -> None:
        self.plant = plant
        self._lock = threading.Lock()
        self._last_ns: int | None = None

    def advance_to(self, time_ns: int) -> None:
        """Step to `time_ns`; the same or an earlier instant does nothing."""
        with self._lock:
            if self._last_ns is not None and time_ns > self._last_ns:
                self.plant.advance((time_ns - self._last_ns) / 1e9)
            if self._last_ns is None or time_ns > self._last_ns:
                self._last_ns = time_ns


_advancers: weakref.WeakKeyDictionary[Plant, Advancer] = weakref.WeakKeyDictionary()
_pinned: dict[int, tuple[Plant, Advancer]] = {}  # plants that cannot be weakly referenced
_advancers_lock = threading.Lock()


def advancer(plant: Plant) -> Advancer:
    """The one [Advancer][flyball_sim.plant.Advancer] for `plant`, shared by all its readers.

    Keyed on the model itself, looking through [Noisy][flyball_sim.plant.Noisy]:
    two readers that wrap one plant in noise of their own still share its time.
    """
    while isinstance(plant, Noisy):
        plant = plant.plant
    with _advancers_lock:
        try:
            found = _advancers.get(plant)
        except TypeError:  # no __weakref__ slot: keep it alive with its advancer
            held = _pinned.get(id(plant))
            if held is None:
                held = _pinned[id(plant)] = (plant, Advancer(plant))
            return held[1]
        if found is None:
            found = _advancers[plant] = Advancer(plant)
        return found


@runtime_checkable
class MultiPlant(Protocol):
    """A plant with named inputs and outputs, stepped once per instant however many read it."""

    @property
    def inputs(self) -> dict[str, float]: ...

    @property
    def output_names(self) -> tuple[str, ...]: ...

    def output(self, port: str) -> float: ...

    def advance_to(self, time_ns: int) -> None:
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

    def advance_to(self, time_ns: int) -> None:
        self.plant.advance_to(time_ns)

    def advance(self, dt_s: float) -> float:
        raise TypeError(
            "a multi-port plant is advanced to a time, not by an interval: use advance_to()"
        )

    def feedforward(self, demand: float) -> float:
        if self.input_name is None:
            raise AttributeError("this port has no input")
        return self.plant.feedforward(self.input_name, demand)

    def inverse_feedforward(self, drive: float) -> float:
        if self.input_name is None:
            raise AttributeError("this port has no input")
        return self.plant.inverse_feedforward(self.input_name, drive)


__all__ = [
    "Advancer",
    "Fopdt",
    "Integrator",
    "Lag",
    "MultiPlant",
    "Noisy",
    "Plant",
    "Port",
    "advancer",
]
