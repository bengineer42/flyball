from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Self, overload

from humctrl.manager import State
from humctrl.pumps import AbsoluteFlows
from humctrl.sensors import Reader, Reading
from humctrl.typing import Percent

# class Recorder:
#     namespace:
#     tables: dict[str, Table]


# class Table(Protocol):
#     columns: dict[str, Any]
#     def record_row(self, **kwargs): ...


@dataclass
class Record:
    time_ns: int
    process_humidity: float | None
    process_temperature: float | None
    wet_flow: float | None
    dry_flow: float | None
    flow_units: str | None
    target_humidity: float | None
    sensor_readings: list[Reading] | None

    @classmethod
    def from_process_reading(
        cls,
        process_reading: Reader,
        wet_flow: float | None = None,
        dry_flow: float | None = None,
        flow_units: str | None = None,
        target_humidity: float | None = None,
    ) -> Self:
        return cls(
            time_ns=process_reading.time_ns,
            process_humidity=process_reading.humidity,
            process_temperature=process_reading.temperature,
            wet_flow=wet_flow,
            dry_flow=dry_flow,
            flow_units=flow_units,
            target_humidity=target_humidity,
            sensor_readings=process_reading.raw,
        )

    def set_flows(self, flows: AbsoluteFlows):
        self.wet_flow = flows.wet
        self.dry_flow = flows.dry
        self.flow_units = flows.units


class Recorder(Protocol):
    def record(
        self,
        time_ns: int,
        reading: Reader | None = None,
        flows: AbsoluteFlows | None = None,
        target_humidity: Percent | None = None,
    ): ...
    def record_state(
        self,
        state: State,
    ): ...
    def add_flag(self, flag: str, time: float): ...
