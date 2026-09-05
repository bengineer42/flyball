from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any, Protocol

from pydantic import PlainSerializer

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
    def time(self) -> float:
        return self.time_ns / 1e9


@dataclass(frozen=True, slots=True)
class FusedReading(Reading):
    members: list[Reading] = field(default_factory=list)


class Reader(Protocol):
    def read(self) -> Reading: ...


class ReaderSource(Enum):
    WET = "wet"
    DRY = "dry"
    PROCESS = "process"


#: What a line last reported: a reading, the failure that stopped it, or
#: nothing yet. An exception cannot cross the wire, so it goes out as its
#: message.
type Reported = Annotated[
    Reading | Any | None,
    PlainSerializer(lambda value: str(value) if isinstance(value, Exception) else value),
]


@dataclass(frozen=True, slots=True)
class Readings:
    process: Reported = None
    dry: Reported = None
    wet: Reported = None

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
