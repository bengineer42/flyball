from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple, Protocol

from humctrl.core import Clock, HardwareError, HumCtrlError, NotFoundError, Percent

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
class HTReading:
    time_ns: int
    humidity: Percent
    temperature: float
    source: str | None = None

    @property
    def seconds(self) -> float:
        return self.time_ns / 1e9


def to_percent(value: HTReading | Percent) -> Percent:
    if isinstance(value, HTReading):
        return value.humidity
    return value


@dataclass(frozen=True, slots=True)
class FusedReading(HTReading):
    members: list[HTReading] = field(default_factory=list)


class Reader(Protocol):
    def read(self) -> HTReading: ...


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


class HTReadings(NamedTuple):
    process: HTReading | Exception | None = None
    dry: HTReading | Exception | None = None
    wet: HTReading | Exception | None = None

    def __iter__(self):
        yield (ReaderSource.PROCESS, self.process)
        yield (ReaderSource.DRY, self.dry)
        yield (ReaderSource.WET, self.wet)


class Readers(Protocol):
    clock: Clock

    def attach_clock(self, clock: Clock) -> None:
        self.clock = clock

    def read_all(self) -> HTReadings:
        return HTReadings(
            process=self.read_process(),
            dry=self.read_dry(),
            wet=self.read_wet(),
        )

    def read_process(self) -> HTReading | Exception | None:
        return None

    def read_wet(self) -> HTReading | Exception | None:
        return None

    def read_dry(self) -> HTReading | Exception | None:
        return None

    def read(self, process: bool = True, wet: bool = True, dry: bool = True) -> HTReadings:
        dry_reading = self.read_dry() if dry else None
        wet_reading = self.read_wet() if wet else None
        process_reading = self.read_process() if process else None
        return HTReadings(process=process_reading, dry=dry_reading, wet=wet_reading)
