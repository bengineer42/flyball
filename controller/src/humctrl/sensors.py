from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ReadPolicy(Enum):
    CONTINUOUS = 1
    RATE = 3


@dataclass(frozen=True, slots=True)
class HTReading:
    time_ns: int
    humidity: float
    temperature: float


@dataclass(frozen=True, slots=True)
class ProcessorReading:
    time_ns: int
    humidity: float
    temperature: float
    raw: list[tuple[str, HTReading]] | None


class HTSensors(Protocol):
    def read(self) -> ProcessorReading: ...


class HTSensor(Protocol):
    def read(self) -> HTReading: ...
