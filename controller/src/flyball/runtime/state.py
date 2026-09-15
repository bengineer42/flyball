from dataclasses import dataclass

from flyball.control.loop import LoopSpec, LoopState
from flyball.core.reading import Measurand, Point, Sample, Source
from flyball.core.sink import ActuatorView
from flyball.runtime.actuator import ActuatorState


@dataclass(slots=True, frozen=True)
class SourceState:
    sample: Sample | None
    readings: dict[Measurand, Point]
    exception: Exception | None


@dataclass(slots=True, frozen=True)
class RigSpec:
    loops: dict[str, LoopSpec]


@dataclass(slots=True, frozen=True)
class RigState:
    time_ns: int
    loops: dict[str, LoopState]
    actuators: dict[str, ActuatorState]
    sources: dict[Source, SourceState]


class RigView:
    loops: dict[str, LoopState]
    actuators: dict[str, ActuatorView]
    sources: dict[Source, SourceState]
