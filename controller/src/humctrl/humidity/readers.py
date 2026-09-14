from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from humctrl.core import (
    Channel,
    Labelled,
    Percent,
    Quantity,
    Sample,
    Source,
)
from humctrl.core.reading import Reader

Temperature = Quantity("temperature", "°C")
Humidity = Quantity("humidity", "%RH")

HTQuantities = (Humidity, Temperature)


class HTReaderSource(Labelled):
    DRY = "dry"
    WET = "wet"
    PROCESS = "process"


class HTSource(Source):
    __slots__ = ()

    def __init__(self, name: str) -> None:
        super().__init__(name, HTQuantities)

    @property
    def humidity(self) -> Channel:
        return self[Humidity]

    @property
    def temperature(self) -> Channel:
        return self[Temperature]


@dataclass(frozen=True, slots=True)
class HTReading(Sample):
    source: HTSource

    @classmethod
    def of(
        cls, source: HTSource, seq: int, time_ns: int, humidity: Percent, temperature: float
    ) -> HTReading:
        return cls(source, seq, time_ns, {Humidity: humidity, Temperature: temperature})

    @property
    def humidity(self) -> Percent:
        return self.values[Humidity]

    @property
    def temperature(self) -> float:
        return self.values[Temperature]


class HTSetReader(Reader):
    _sources: dict[HTReaderSource, HTSource]

    @property
    def process(self) -> HTSource | None:
        return self._sources.get(HTReaderSource.PROCESS)

    @property
    def dry(self) -> HTSource | None:
        return self._sources.get(HTReaderSource.DRY)

    @property
    def wet(self) -> HTSource | None:
        return self._sources.get(HTReaderSource.WET)

    def read_process(self, time_ns: int) -> HTReading | Exception | None:
        return None

    def read_dry(self, time_ns: int) -> HTReading | Exception | None:
        return None

    def read_wet(self, time_ns: int) -> HTReading | Exception | None:
        return None

    def read(self, time_ns: int) -> Iterable[HTReading | Exception]: ...
