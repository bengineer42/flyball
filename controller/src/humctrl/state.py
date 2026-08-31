from dataclasses import dataclass
from typing import overload

from humctrl.clock import Duration, Time
from humctrl.manager import Lines, Stream
from humctrl.pumps import AbsoluteFlows, Efforts, MaxFlows, PumpsOutput
from humctrl.sensors import Reading
from humctrl.typing import Percent


class Setup:
    loop_time: Duration
    expected_humidities: Lines[Percent | None]
    max_flows: MaxFlows | None


@dataclass(slots=True)
class State:
    time: Time
    pumps: PumpsOutput | None = None
    process_reading: Reading | None = None
    dry_reading: Reading | None = None
    wet_reading: Reading | None = None
    target_humidity: Percent | None = None
    target_stream: Stream | None = None
    flow_humidity: Percent | None = None

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

    def update_process_reading(self, reading: Reading | None = None):
        self.process_reading = reading
        if reading is not None:
            self.update_time(Time(nanoseconds=reading.time_ns))

    def update_dry_reading(self, reading: Reading | None = None):
        self.dry_reading = reading
        if reading is not None:
            self.update_time(Time(nanoseconds=reading.time_ns))

    def update_wet_reading(self, reading: Reading | None = None):
        self.wet_reading = reading
        if reading is not None:
            self.update_time(Time(nanoseconds=reading.time_ns))

    def update_pumps(self, outputs: PumpsOutput | None, time: Time | None = None):
        if outputs is not None:
            self.pumps = outputs
        if time is not None:
            self.time = time

    def update_target_humidity(self, target_humidity: Percent | None, time: Time | None = None):
        self.target_humidity = target_humidity
        if time is not None:
            self.time = time

    def update_flow_humidity(self, flow_humidity: Percent | None, time: Time | None = None):
        self.flow_humidity = flow_humidity
        if time is not None:
            self.time = time

    def update_target_stream(self, target_stream: Stream | None, time: Time | None = None):
        self.target_stream = target_stream
        if time is not None:
            self.time = time

    @overload
    def update(
        self,
        *,
        time: Time | None,
        pumps: PumpsOutput | None = None,
        process_reading: Reading | None = None,
        dry_reading: Reading | None = None,
        wet_reading: Reading | None = None,
        target_humidity: Percent | None = None,
        flow_humidity: Percent | None = None,
    ): ...

    @overload
    def update(
        self,
        *,
        time: float | int | None = None,
        nanoseconds: int | None = None,
        pumps: PumpsOutput | None = None,
        process_reading: Reading | None = None,
        dry_reading: Reading | None = None,
        wet_reading: Reading | None = None,
        target_humidity: Percent | None = None,
        flow_humidity: Percent | None = None,
    ): ...

    def update(
        self,
        *,
        time: Time | float | int | None = None,
        nanoseconds: int | None = None,
        pumps: PumpsOutput | None = None,
        process_reading: Reading | None = None,
        dry_reading: Reading | None = None,
        wet_reading: Reading | None = None,
        target_humidity: Percent | None = None,
        flow_humidity: Percent | None = None,
    ):
        if pumps is not None:
            self.pumps = pumps
        if process_reading is not None:
            self.update_process_reading(process_reading)
        if target_humidity is not None:
            self.target_humidity = target_humidity
        if dry_reading is not None:
            self.update_dry_reading(dry_reading)
        if wet_reading is not None:
            self.update_wet_reading(wet_reading)
        if flow_humidity is not None:
            self.flow_humidity = flow_humidity
        if time is not None or nanoseconds is not None:
            self._update_time(time, nanoseconds)
