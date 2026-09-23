"""A simulated oven: a probe, a heater, one controller between them. Runs anywhere."""

from collections.abc import Iterator

from flyball.control import PI
from flyball.foundation.device.descriptors import Demand
from flyball.foundation.device.device import Committable, Readable, Readout
from flyball.foundation.quantities.quantity import Quantity
from flyball.foundation.device.signal import Sample
from flyball.foundation.quantities.si import Celsius
from flyball.rig import Rig
from flyball_sim import Lag, SteppedClock

# What is measured, independent of any device: a name and a unit.
TEMPERATURE = Quantity("temperature", Celsius)


class Probe(Readable):
    """Reads the oven: one signal, readable and published."""

    temperature = Readout("temperature", "Temperature", TEMPERATURE, range=(0.0, 300.0), precision=1)

    def __init__(self, name: str, oven: Lag) -> None:
        super().__init__(name)
        self.oven = oven
        self._last_ns: int | None = None

    def read(self, time_ns: int, node=None) -> Iterator[Sample]:
        dt = 0.0 if self._last_ns is None else (time_ns - self._last_ns) / 1e9
        self._last_ns = time_ns
        yield self.sample(time_ns, temperature=self.oven.step(dt))


# --8<-- [start:heater]
class Heater(Committable):
    """Drives the oven: whatever demand it is given, the plant chases."""

    demand = Demand("demand", "Demand", TEMPERATURE)

    def __init__(self, name: str, oven: Lag) -> None:
        super().__init__(name)
        self.oven = oven

    def write_signal(self, signal, value: float) -> None:
        self.oven.input = value
# --8<-- [end:heater]


# --8<-- [start:build]
def build(clock: SteppedClock, period: float | None = None) -> Rig:
    """A rig with a probe, a heater, and the default controller between them.

    With `period`, the probe is polled on a thread every `period` seconds;
    with none (the default), nothing polls it and a caller drives it by
    hand with `rig.read(probe.root, fresh=True)`.
    """
    oven = Lag(tau_s=30.0, value=20.0)
    rig = Rig()
    rig.clock = clock
    probe, heater = Probe("probe", oven), Heater("heater", oven)
    probe.poll_s = period
    rig.add_device(probe)
    rig.add_device(heater)
    rig.attach_controller(
        heater.demand,
        probe.temperature,
        law=PI(kp=0.5, ki=0.05),
        default=True,
    )
    return rig
# --8<-- [end:build]


if __name__ == "__main__":
    # --8<-- [start:main]
    clock = SteppedClock(0)
    rig = build(clock)
    controller = rig.controllers.resolve(None)
    controller.regulate(100.0)
    for _ in range(120):
        clock.advance(1.0)
        rig.read(rig.devices["probe"].root, fresh=True)
    reading = rig.read(rig.devices["probe"].temperature)
    print(f"after 2 min: {reading.value:.1f} °C, output {controller.output:.1f}")
    # --8<-- [end:main]
