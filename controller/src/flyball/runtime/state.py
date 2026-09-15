"""The rig as data, in three tiers.

**config** is the rig's structure and is not a model here. **settings** change
on a command and are published when they do. **state** changes every tick or
apply. A [RigView][flyball.runtime.state.RigView] joins settings and state at
one instant, as [LoopView][flyball.control.LoopView] does for one loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flyball.control import LoopSettings, LoopState, LoopView
from flyball.core.reading import Measurand, Point, Sample
from flyball.core.sink import ActuatorSettings, ActuatorState, ActuatorView


@dataclass(slots=True, frozen=True)
class SourceState:
    sample: Sample | None
    readings: dict[Measurand, Point]
    exception: Exception | None


@dataclass(slots=True, frozen=True)
class RigSettings:
    loops: dict[str, LoopSettings]
    actuators: dict[str, ActuatorSettings]


@dataclass(slots=True, frozen=True)
class RigState:
    time_ns: int
    loops: dict[str, LoopState]
    actuators: dict[str, ActuatorState]
    sources: dict[str, SourceState]


@dataclass(slots=True, frozen=True)
class RigView:
    time_ns: int
    loops: dict[str, LoopView]
    actuators: dict[str, ActuatorView[Any, Any, Any]]
    sources: dict[str, SourceState]
