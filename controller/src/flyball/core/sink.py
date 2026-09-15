"""Sinks, observers and actuators.

An actuator is a :class:`~flyball.core.device.Device` that a loop drives:
it takes a demand, commits on ``apply``, and reports what it is doing. The
three tiers -- config, settings, state -- and :func:`command` are the
device's; see that module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

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
    """Takes something in, commits on ``apply``. Base for actuators and buffering observers.

    ``name`` identifies it in the rig, on the wire and in a recording -- for
    an actuator it is also the name of the loop driving it.
    """

    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def apply(self) -> None:
        """Commit whatever was handed over since the last apply. Default: nothing."""


class Observer[S: Sample | Reading]:
    """Hears samples from the sources and channels it names.

    ``observe`` is called once per sample whose source, or any of whose
    channels, is in ``observes`` -- an observer keyed on a channel still gets
    the whole sample and picks its measurand. ``touches`` lists the sinks the
    rig should ``apply`` after a delivery in which this observer fired.
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


class ActuatorSettings(DeviceSettings):
    """What an operator can re-set while an actuator runs."""


class ActuatorConfig[A: "Actuator"](DeviceConfig[A]):
    """What an actuator is built from, and what builds it."""


ActuatorView = DeviceView


class Actuator(Device, Sink):
    """A device a loop drives.

    ``demand_unit`` is what :meth:`set_demand` is in -- the loop's measurand,
    or None for a unitless demand.
    """

    config_type: ClassVar[type[DeviceConfig[Any]]] = ActuatorConfig
    settings_type: ClassVar[type[DeviceSettings]] = ActuatorSettings
    state_type: ClassVar[type[DeviceState]] = ActuatorState
    demand_unit: ClassVar[Unit | None] = None

    def __init__(self, name: str) -> None:
        Device.__init__(self, name)

    def set_demand(self, demand: float) -> float | None:
        """Take a demand; return the value the loop should expect, if it differs."""
        raise NotImplementedError

    @property
    def state(self) -> ActuatorState:
        """Default: just the demand is unknown. Override with what the device knows now."""
        return ActuatorState()
