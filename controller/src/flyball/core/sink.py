"""Sinks, observers and actuators.

An actuator is a [Device][flyball.core.device.Device] a loop drives: it takes
a demand, commits on `apply`, and reports what it is doing. Tiers and
[command][flyball.core.device.command] are the device's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import Field

from .device import (
    RESERVED_NAMES,
    CommandSpec,
    Device,
    DeviceConfig,
    DeviceSettings,
    DeviceState,
    DeviceView,
    command,
)
from .reading import Channel, Reading, Sample, Source
from .units import Unit

__all__ = [
    "RESERVED_NAMES",
    "Actuator",
    "ActuatorConfig",
    "ActuatorSettings",
    "ActuatorState",
    "ActuatorView",
    "CommandSpec",
    "Observer",
    "Sink",
    "command",
]


class Sink:
    """Takes something in, commits on `apply`. Base for actuators and buffering observers.

    `name` identifies it in the rig, on the wire and in a recording -- for
    an actuator it is also the name of the loop driving it.
    """

    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def apply(self) -> None:
        """Commit whatever was handed over since the last apply. Default: nothing."""


class Observer[S: Sample | Reading]:
    """Hears samples from the sources and channels it names.

    `observe` gets the whole sample whenever its source or any of its channels
    is in `observes`. `touches` lists the sinks the rig should `apply` after a
    delivery in which this observer fired.
    """

    name: str
    observes: frozenset[Source | Channel]
    touches: frozenset[Sink] = frozenset()

    def observe(self, sample: S) -> None:
        raise NotImplementedError


@dataclass(frozen=True, slots=True, kw_only=True)
class ActuatorState(DeviceState):
    """What an actuator is doing now. Subclasses add the device's own fields."""

    demand: float | None = None
    """The last demand handed over by the loop, in the loop's measurand unit."""
    output_range: tuple[float, float] | None = None
    """What the demand can achieve, in the actuator's own unit; None if unknown.

    A sim actuator derives it from its drive `limits` and its Watt-or-`of
    full` scale; a real one states it in its config, since there is no
    generic way to derive a physical range from an arbitrary instrument.
    """


class ActuatorSettings(DeviceSettings):
    """What an operator can re-set while an actuator runs."""


class ActuatorConfig[A: "Actuator"](DeviceConfig[A]):
    """What an actuator is built from, and what builds it."""

    output_range: tuple[float, float] | None = Field(
        default=None,
        description="The demand's achievable range, in the actuator's own unit."
        " A sim actuator works this out itself; a real one that cannot states it here.",
    )


ActuatorView = DeviceView


class Actuator(Device, Sink):
    """A device a loop drives.

    `demand_unit` is what [set_demand][flyball.core.sink.Actuator.set_demand]
    takes; None if unitless.
    """

    config_type: ClassVar[type[DeviceConfig[Any]]] = ActuatorConfig
    settings_type: ClassVar[type[DeviceSettings]] = ActuatorSettings
    state_type: ClassVar[type[DeviceState]] = ActuatorState
    demand_unit: ClassVar[Unit | None] = None
    output_range: ClassVar[tuple[float, float] | None] = None
    """The demand's achievable range, in `demand_unit`; None if unknown. Per instance,
    like `demand_unit`: a sim actuator works it out, a rig file may state it for a real
    one (`runtime.config` copies a config's `output_range` onto the actuator it builds)."""

    def __init__(self, name: str) -> None:
        Device.__init__(self, name)

    def set_demand(self, demand: float) -> float | None:
        """Take a demand; return the value the loop should expect, if it differs."""
        raise NotImplementedError

    @command(tag="demand")
    def demand_directly(self, demand: float) -> float | None:
        """Ask for a value directly, in the actuator's own unit, as a loop would.

        Every actuator has this: it is the one thing an operator can do to an
        actuator by hand. Refused by the server while a loop is regulating
        the actuator -- stop the loop first, or it would just be overwritten
        on the next tick.
        """
        return self.set_demand(demand)

    @property
    def state(self) -> ActuatorState:
        """Default: just the demand is unknown. Override with what the device knows now."""
        return ActuatorState(output_range=self.output_range)
