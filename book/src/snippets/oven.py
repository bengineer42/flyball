"""A simulated oven: a probe, a heater, one controller between them. Runs anywhere."""

from collections.abc import Iterator

from flyball.control import PI
from flyball.core import Access, Quantity, Sample, SignalSpec
from flyball.core.device import Device
from flyball.core.units.si import Celsius
from flyball.runtime import Rig
from flyball.sim import Lag, SteppedClock

# What is measured, independent of any device: a name and a unit.
TEMPERATURE = Quantity("temperature", Celsius)


class Probe(Device):
    """Reads the oven: one signal, readable and publishing."""

    TREE = (
        SignalSpec(
            name="temperature",
            quantity=TEMPERATURE,
            access=Access.RP,
            range=(0.0, 300.0),
            precision=1,
        ),
    )

    def __init__(self, name: str, oven: Lag) -> None:
        super().__init__(name)
        self.oven = oven
        self._last_ns: int | None = None

    def read(self, time_ns: int, node=None) -> Iterator[Sample]:
        dt = 0.0 if self._last_ns is None else (time_ns - self._last_ns) / 1e9
        self._last_ns = time_ns
        yield Sample(self.root, time_ns, {self.signals["temperature"]: self.oven.step(dt)})


class Heater(Device):
    """Drives the oven: whatever demand it is given, the plant chases."""

    TREE = (SignalSpec(name="demand", quantity=TEMPERATURE, access=Access.W),)

    def __init__(self, name: str, oven: Lag) -> None:
        super().__init__(name)
        self.oven = oven

    def write_signal(self, signal, value: float) -> None:
        self.oven.input = value


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
        heater.signals["demand"],
        probe.signals["temperature"],
        law=PI(kp=0.5, ki=0.05),
        default=True,
    )
    return rig


if __name__ == "__main__":
    clock = SteppedClock(0)
    rig = build(clock)
    controller = rig.controllers.resolve(None)
    controller.regulate(100.0)
    for _ in range(120):
        clock.advance(1.0)
        rig.read(rig.devices["probe"].root, fresh=True)
    reading = rig.read(rig.devices["probe"].signals["temperature"])
    print(f"after 2 min: {reading.value:.1f} °C, demand {controller.demand:.1f}")
