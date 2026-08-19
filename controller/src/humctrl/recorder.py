from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Self, overload

from humctrl.pumps import AbsoluteFlows
from humctrl.sensors import HumidityTemperatureReading, ProcessorReading

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
    sensor_readings: list[tuple[str, HumidityTemperatureReading]] | None

    @classmethod
    def from_process_reading(
        cls,
        process_reading: ProcessorReading,
        wet_flow: float | None,
        dry_flow: float | None,
        flow_units: str | None,
        target_humidity: float | None,
    ) -> Self:
        return cls(
            time=process_reading.time,
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


class HTRecorder(Protocol):
    @overload
    def record(self, record: Record, /): ...

    @overload
    def record(
        self,
        time_ns: int,
        *,
        process_humidity: float | None,
        process_temperature: float | None,
        wet_flow: float | None,
        dry_flow: float | None,
        flow_units: str | None,
        target_humidity: float | None,
        sensor_readings: list[tuple[str, HumidityTemperatureReading]] | None,
    ): ...

    def record(
        self,
        value,
        process_humidity: float | None,
        process_temperature: float | None,
        wet_flow: float | None,
        dry_flow: float | None,
        flow_units: str | None,
        target_humidity: float | None,
        sensor_readings: list[tuple[str, HumidityTemperatureReading]] | None,
    ):
        if isinstance(value, Record):
            self._record(
                value.time_ns,
                process_humidity=value.process_humidity,
                process_temperature=value.process_temperature,
                wet_flow=value.wet_flow,
                dry_flow=value.dry_flow,
                flow_units=value.flow_units,
                target_humidity=value.target_humidity,
                sensor_readings=value.sensor_readings,
            )
        else:
            self._record(
                value,
                process_humidity=process_humidity,
                process_temperature=process_temperature,
                wet_flow=wet_flow,
                dry_flow=dry_flow,
                flow_units=flow_units,
                target_humidity=target_humidity,
                sensor_readings=sensor_readings,
            )

    def _record(
        self,
        time_ns: int,
        *,
        process_humidity: float | None,
        process_temperature: float | None,
        wet_flow: float | None,
        dry_flow: float | None,
        flow_units: str | None,
        target_humidity: float | None,
        sensor_readings: list[tuple[str, HumidityTemperatureReading]] | None,
    ): ...

    def add_flag(self, flag: str, time: float): ...
