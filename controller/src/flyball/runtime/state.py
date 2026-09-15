from dataclasses import dataclass
from typing import Any

from flyball.control.loop import LoopSpec, LoopState
from flyball.core.reading import Measurand, Point, Sample, Source


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
    actuators: dict[str, Any]
    sources: dict[Source, SourceState]
