"""A simulated oven: one heater, one probe, one loop. Runs anywhere."""

from flyball.control import PI
from flyball.core import Actuator, Clock, Measurand, Source
from flyball.core.sink import ActuatorState
from flyball.core.units.si import Celsius
from flyball.runtime import Rig
from flyball.sim import FunctionReader, Lag, SteppedClock

# What is measured, and by what. Declared once, process-wide.
TEMPERATURE = Measurand("temperature", Celsius, range=(0.0, 300.0), precision=1)
probe = Source("probe", (TEMPERATURE,))


class Heater(Actuator):
    """Drives a lag plant: whatever demand it is given, the oven chases."""

    demand_unit = Celsius

    def __init__(self, name: str, oven: Lag) -> None:
        super().__init__(name)
        self.oven = oven
        self.demand: float | None = None

    def set_demand(self, demand: float) -> float | None:
        self.demand = demand
        return demand  # what it expects to deliver; here, exactly what was asked

    @property
    def state(self) -> ActuatorState:
        return ActuatorState(demand=self.demand)


def build(clock: Clock, period: float | None = None) -> Rig:
    """A rig with one loop. With `period`, the probe is polled on a thread."""
    oven = Lag(tau_s=30.0, value=20.0)
    heater = Heater("heater", oven)
    last_ns = clock.now_ns()

    def model(time_ns: int) -> dict[Measurand, float]:
        nonlocal last_ns
        dt, last_ns = (time_ns - last_ns) / 1e9, time_ns
        return {TEMPERATURE: oven.step(heater.demand or 20.0, dt)}

    rig = Rig()
    rig.clock = clock
    rig.start_reader(FunctionReader("probe_reader", {probe: model}), period=period)
    rig.attach_loop(probe[TEMPERATURE], heater, law=PI(kp=0.5, ki=0.05), default=True)
    return rig


if __name__ == "__main__":
    clock = SteppedClock(0)
    rig = build(clock)
    loop = rig.loops["heater"]
    loop.regulate(100.0)
    for _ in range(120):
        clock.advance(1.0)
        rig.read(rig.readers.get("probe_reader"))
    print(f"after 2 min: {loop.reading.value:.1f} °C, demand {loop.demand:.1f}")
