from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple, Protocol

from humctrl.core import (
    Channel,
    HardwareError,
    HumCtrlError,
    Labelled,
    NotFoundError,
    Percent,
    Quantity,
    Sample,
    Source,
)

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


def to_percent(value: HTReading | Percent) -> Percent:
    if isinstance(value, HTReading):
        return value.humidity
    return value


class Reader(Protocol):
    def read(self) -> HTReading: ...


Temperature = Quantity("temperature", "°C")
Humidity = Quantity("humidity", "%RH")

HTQuantities = (Humidity, Temperature)


class HTReaderSource(Labelled):
    DRY = "dry"
    WET = "wet"
    PROCESS = "process"


class HTSource(Source):
    channels: dict[Quantity, Channel]

    def __init__(self, name: HTReaderSource):
        self.declare(Temperature)
        self.declare(Humidity)
        super().__init__(name, HTQuantities)

    @property
    def humidity(self) -> Channel:
        return self.channels[Humidity]

    @property
    def temperature(self) -> Channel:
        return self.channels[Temperature]


@dataclass(frozen=True, slots=True)
class HTReading(Sample):
    source: HTSource

    @classmethod
    def of(cls, time_ns: int, source: HTSource, humidity: Percent, temperature: float) -> HTReading:
        return cls(time_ns, source, {Humidity: humidity, Temperature: temperature})

    @property
    def humidity(self) -> Percent:
        return self.values[Humidity]

    @property
    def temperature(self) -> float:
        return self.values[Temperature]


@dataclass(frozen=True, slots=True)
class FusedReading(HTReading):
    members: list[HTReading] = field(default_factory=list)


class ReadError(Exception):
    source: HTReaderSource
    message: str
    time_ns: int

    def __init__(self, source: HTReaderSource, message: str, time_ns: int) -> None:
        self.source = source
        self.message = message
        self.time_ns = time_ns  # Assuming you want to capture the current time in nanoseconds
        super().__init__(f"Error reading from {source.value} sensor at {time_ns / 1e9}: {message}")


class HTReadings(NamedTuple):
    process: HTReading | Exception | None = None
    dry: HTReading | Exception | None = None
    wet: HTReading | Exception | None = None

    def __iter__(self):
        yield (HTReaderSource.PROCESS, self.process)
        yield (HTReaderSource.DRY, self.dry)
        yield (HTReaderSource.WET, self.wet)


class HTSetReader(Reader):
    def read_all(self) -> HTReadings:
        return HTReadings(
            process=self.read_process(),
            dry=self.read_dry(),
            wet=self.read_wet(),
        )

    def read_process(self, time_ns: int) -> HTReading | Exception | None:
        return None

    def read_wet(self, time_ns: int) -> HTReading | Exception | None:
        return None

    def read_dry(self, time_ns: int) -> HTReading | Exception | None:
        return None

    def read(self, process: bool = True, wet: bool = True, dry: bool = True) -> HTReadings:
        dry_reading = self.read_dry() if dry else None
        wet_reading = self.read_wet() if wet else None
        process_reading = self.read_process() if process else None
        return HTReadings(process=process_reading, dry=dry_reading, wet=wet_reading)
