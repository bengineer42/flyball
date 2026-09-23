"""Two devices reporting the same two signals: one polled, one pushed."""

import random
from collections.abc import Iterator

from flyball.foundation import Quantity, Sample
from flyball.foundation.device.device import Output, Readable
from flyball.foundation.quantities.si import Celsius, Pascal

# --8<-- [start:quantities]
# What is measured, independent of any device: a name and a unit, nothing else.
TEMPERATURE = Quantity("temperature", Celsius)
PRESSURE = Quantity("pressure", Pascal)
# --8<-- [end:quantities]


# --8<-- [start:polled-signals]
class PolledWeather(Readable):
    """The rig calls `read` on a period and delivers what comes back."""

    # The tree: one descriptor per signal. Range and precision are the
    # *signal's*, declared here: two stations may report `temperature`
    # with different bounds. `Output` is RP -- produced, never set.
    temperature = Output("temperature", "Air temperature", TEMPERATURE, range=(-40.0, 125.0), precision=2)
    pressure = Output("pressure", "Barometric pressure", PRESSURE, range=(30_000.0, 110_000.0), precision=0)
    # --8<-- [end:polled-signals]

    # --8<-- [start:polled-read]
    def read(self, time_ns: int, node=None) -> Iterator[Sample]:
        yield self.sample(
            time_ns,
            temperature=20.0 + random.gauss(0, 0.1),
            pressure=101_325.0 + random.gauss(0, 10),
        )
    # --8<-- [end:polled-read]


# --8<-- [start:pushed]
class PushedWeather(Readable):
    """Something else produces values; this device hands them to the rig as they arrive."""

    temperature = Output("temperature", "Air temperature", TEMPERATURE, range=(-40.0, 125.0), precision=2)
    pressure = Output("pressure", "Barometric pressure", PRESSURE, range=(30_000.0, 110_000.0), precision=0)

    def read(self, time_ns: int, node=None) -> Iterator[Sample]:
        yield from ()  # nothing to poll: every value arrives by push

    def on_packet(self, temperature: float, pressure: float, time_ns: int) -> None:
        # Called from a serial thread, a callback, a subscription -- any
        # thread: `push` hands one sample to the rig under its own lock.
        self.push(time_ns, temperature=temperature, pressure=pressure)
# --8<-- [end:pushed]
