from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple, Protocol

from humctrl.error import HardwareError, HumCtrlError, NotFoundError
from humctrl.typing import Percent

# region Exceptions


class SensorError(HumCtrlError):
    """Base for everything humctrl.readers raises."""


class SensorReadError(SensorError, HardwareError):
    """The sensor is fitted and the read failed. Usually transient."""

    def __init__(self, sensor_name: str, message: str) -> None:
        super().__init__(f"Error reading from sensor {sensor_name!r}: {message}")


class SensorNotSetError(SensorError, NotFoundError):
    """This rig has no such sensor. Distinct from one fitted but not yet read."""

    def __init__(self, sensor_name: str) -> None:
        super().__init__(f"Sensor {sensor_name!r} not set.")


# endregion


@dataclass(frozen=True, slots=True)
class Reading:
    time_ns: int
    humidity: Percent
    temperature: float
    source: str | None = None

    @property
    def seconds(self) -> float:
        return self.time_ns / 1e9


def to_percent(value: Reading | Percent) -> Percent:
    if isinstance(value, Reading):
        return value.humidity
    return value


@dataclass(frozen=True, slots=True)
class FusedReading(Reading):
    members: list[Reading] = field(default_factory=list)


class Reader(Protocol):
    def read(self) -> Reading: ...


class ReaderSource(Enum):
    WET = "wet"
    DRY = "dry"
    PROCESS = "process"


class ReadError(Exception):
    source: ReaderSource
    message: str
    time_ns: int

    def __init__(self, source: ReaderSource, message: str, time_ns: int) -> None:
        self.source = source
        self.message = message
        self.time_ns = time_ns  # Assuming you want to capture the current time in nanoseconds
        super().__init__(f"Error reading from {source.value} sensor at {time_ns / 1e9}: {message}")


class Readings(NamedTuple):
    process: Reading | Exception | None = None
    dry: Reading | Exception | None = None
    wet: Reading | Exception | None = None

    def __iter__(self):
        yield (ReaderSource.PROCESS, self.process)
        yield (ReaderSource.DRY, self.dry)
        yield (ReaderSource.WET, self.wet)


class Readers(Protocol):
    @property
    def process_available(self) -> bool: ...
    @property
    def dry_available(self) -> bool: ...
    @property
    def wet_available(self) -> bool: ...
    def read_all(self) -> Readings: ...
    def read_process(self) -> Reading: ...
    def read_wet(self) -> Reading: ...
    def read_dry(self) -> Reading: ...
    def read(self, process: bool = True, wet: bool = True, dry: bool = True) -> Readings: ...


class PolledReader(Protocol):
    def read(self) -> Reading: ...
