from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from humctrl.typing import Percent
from humctrl.utils import Unset, UnsetType

# region Exceptions


class SensorError(Exception): ...


class SensorReadError(SensorError):
    def __init__(self, sensor_name: str, message: str):
        super().__init__(f"Error reading from sensor '{sensor_name}': {message}")


# endregion


@dataclass(frozen=True, slots=True)
class Reading:
    time_ns: int
    humidity: Percent
    temperature: float
    source: str | None = None

    @property
    def time(self) -> float:
        return self.time_ns / 1e9


@dataclass(frozen=True, slots=True)
class FusedReading(Reading):
    members: list[Reading] = field(default_factory=list)


class Reader(Protocol):
    def read(self) -> Reading: ...


class ReadersInterface(Protocol):
    def read(self, /, **kwargs) -> dict[str, Reading]: ...


class Readings:
    process: Reading | UnsetType | None = Unset
    dry: Reading | UnsetType | None = Unset
    wet: Reading | UnsetType | None = Unset


class Readers(Protocol):
    def read_all(self) -> Readings: ...

    def read_process(self) -> Reading: ...

    def read_wet(self) -> Reading: ...

    def read_dry(self) -> Reading: ...

    def read(self, process: bool = True, wet: bool = True, dry: bool = True) -> Readings: ...


class PolledReader(Protocol):
    def read(self) -> Reading: ...
