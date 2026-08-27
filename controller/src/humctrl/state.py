from dataclasses import dataclass
from typing import overload

from humctrl.clock import Time
from humctrl.pumps import AbsoluteFlows
from humctrl.sensors import Reading
from humctrl.typing import Percent


@dataclass(slots=True)
class State:
    time: Time
    flows: AbsoluteFlows | None = None
    process_reading: Reading | None = None
    dry_reading: Reading | None = None
    wet_reading: Reading | None = None
    target_humidity: Percent | None = None

    @overload
    def update_time(self, time: Time): ...

    @overload
    def update_time(self, time: float | int | None = None, nanoseconds: int | None = 0): ...

    def update_time(self, time: Time | float | int | None = 0, nanoseconds: int | None = 0):
        self._update_time(time, nanoseconds)

    def _update_time(self, time: Time | float | int | None = 0, nanoseconds: int | None = 0):
        if not isinstance(time, Time):
            time = Time(time, nanoseconds)
        if self.time is None or time > self.time:
            self.time = time

    def update_process_reading(self, reading: Reading):
        self.process_reading = reading
        self.update_time(Time(nanoseconds=reading.time_ns))

    def update_dry_reading(self, reading: Reading):
        self.dry_reading = reading
        self.update_time(Time(nanoseconds=reading.time_ns))

    def update_wet_reading(self, reading: Reading):
        self.wet_reading = reading
        self.update_time(Time(nanoseconds=reading.time_ns))

    def update_flows(self, flows: AbsoluteFlows | None, time: Time | None = None):
        self.flows = flows
        if time is not None:
            self.time = time

    def set_target_humidity(self, target_humidity: Percent | None, time: Time | None = None):
        self.target_humidity = target_humidity
        if time is not None:
            self.time = time

    @overload
    def update(
        self,
        *,
        time: Time | None,
        flows: AbsoluteFlows | None = None,
        process_reading: Reading | None = None,
        dry_reading: Reading | None = None,
        wet_reading: Reading | None = None,
        target_humidity: Percent | None = None,
    ): ...

    @overload
    def update(
        self,
        *,
        time: float | int | None = None,
        nanoseconds: int | None = None,
        flows: AbsoluteFlows | None = None,
        process_reading: Reading | None = None,
        dry_reading: Reading | None = None,
        wet_reading: Reading | None = None,
        target_humidity: Percent | None = None,
    ): ...

    def update(
        self,
        *,
        time: Time | float | int | None = None,
        nanoseconds: int | None = None,
        flows: AbsoluteFlows | None = None,
        process_reading: Reading | None = None,
        dry_reading: Reading | None = None,
        wet_reading: Reading | None = None,
        target_humidity: Percent | None = None,
    ):
        if flows is not None:
            self.flows = flows
        if process_reading is not None:
            self.update_process_reading(process_reading)
        if target_humidity is not None:
            self.target_humidity = target_humidity
        if dry_reading is not None:
            self.update_dry_reading(dry_reading)
        if wet_reading is not None:
            self.update_wet_reading(wet_reading)
        if time is not None or nanoseconds is not None:
            self._update_time(time, nanoseconds)


class StateSnapshot(State):
    def __init__(self, state: State):
        super().__init__(
            time=state.time,
            flows=state.flows,
            process_reading=state.process_reading,
            dry_reading=state.dry_reading,
            wet_reading=state.wet_reading,
            target_humidity=state.target_humidity,
        )
