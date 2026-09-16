"""Two devices reporting the same two signals: one polled, one pushed."""

import random
from collections.abc import Iterator

from flyball.core import Access, Quantity, Sample, SignalSpec
from flyball.core.device import Device
from flyball.core.units.si import Celsius, Pascal
from flyball.runtime import Rig

# What is measured, independent of any device: a name and a unit, nothing else.
TEMPERATURE = Quantity("temperature", Celsius)
PRESSURE = Quantity("pressure", Pascal)

# Range and precision are the *signal's*, declared where the tree is: two
# weather stations can both report `temperature` with different bounds.
TREE = (
    SignalSpec(
        name="temperature",
        quantity=TEMPERATURE,
        access=Access.RP,
        range=(-40.0, 125.0),
        precision=2,
    ),
    SignalSpec(
        name="pressure",
        quantity=PRESSURE,
        access=Access.RP,
        range=(30_000.0, 110_000.0),
        precision=0,
    ),
)


class PolledWeather(Device):
    """The rig calls `read` on a period and delivers what comes back."""

    TREE = TREE

    def read(self, time_ns: int, node=None) -> Iterator[Sample]:
        yield Sample(
            self.root,
            time_ns,
            {
                self.signals["temperature"]: 20.0 + random.gauss(0, 0.1),
                self.signals["pressure"]: 101_325.0 + random.gauss(0, 10),
            },
        )


class PushedWeather(Device):
    """Something else produces values; this device hands them to the rig as they arrive."""

    TREE = TREE

    def __init__(self, name: str, rig: Rig) -> None:
        super().__init__(name)
        self.rig = rig

    def on_packet(self, temperature: float, pressure: float, time_ns: int) -> None:
        # Called from a serial thread, a callback, a subscription -- any
        # thread: `rig.on_samples` takes the rig's own lock.
        sample = Sample(
            self.root,
            time_ns,
            {self.signals["temperature"]: temperature, self.signals["pressure"]: pressure},
        )
        self.rig.on_samples([sample])
