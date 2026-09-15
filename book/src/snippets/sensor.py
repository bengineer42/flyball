"""Two readers for one source: polled and pushed."""

import random
from collections.abc import Iterable

from flyball.core import Measurand, Sample, Source
from flyball.core.reading import Reader
from flyball.core.units.si import Celsius, Pascal


TEMPERATURE = Measurand("temperature", Celsius, range=(-40.0, 125.0), precision=2)
PRESSURE = Measurand("pressure", Pascal, range=(30_000.0, 110_000.0), precision=0)

# One source, two measurands: every sample carries both, stamped once.
weather = Source("weather", (TEMPERATURE, PRESSURE))


class PolledWeather(Reader):
    """The rig calls `read` on a period and delivers what comes back."""

    def __init__(self, name: str) -> None:
        super().__init__(name, (weather,))

    def read(self, time_ns: int) -> Iterable[Sample]:
        values = {
            TEMPERATURE: 20.0 + random.gauss(0, 0.1),
            PRESSURE: 101_325.0 + random.gauss(0, 10),
        }
        return [Sample(weather, weather.next_seq(), time_ns, values)]


class PushedWeather(Reader):
    """Something else produces values; the reader hands them on as they arrive."""

    def __init__(self, name: str) -> None:
        super().__init__(name, (weather,))

    def on_packet(self, temperature: float, pressure: float, time_ns: int) -> None:
        # Called from a serial thread, a callback, a subscription -- any thread.
        self.push(weather, {TEMPERATURE: temperature, PRESSURE: pressure}, time_ns)
