from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading
import time
from typing import Any, Optional, Protocol, Union
from queue import Queue
from typing import assert_never

from humctrl.clock import Clock
from humctrl.control_law import ControlLaw
from humctrl.pumps import (
    DualPumps,
    AbsoluteFlows,
    Flow,
    FractionChangePolicy,
    RaiseOrClamp,
)
from humctrl.utils import validate_normalised, format_quantity
from humctrl.sensors import HTSensor, HTSensors, SensorReading
from humctrl.recorder import HTRecorder, Record

# class LoopController:
#     sensor: Sensor
#     actuator: Actuator1D
#     control_law: ControlLaw

#     loop_time: float = 0
#     skip_if_missed: bool = False

#     last_loop_start_time: float = 0

#     running: bool
#     target: float

#     def __init__(
#         self,
#         sensor: Sensor,
#         actuator: Actuator1D,
#         control_law: ControlLaw,
#         loop_time: float = 0,
#         skip_if_missed: bool = False,
#     ):
#         self.sensor = sensor
#         self.actuator = actuator
#         self.control_law = control_law
#         self.loop_time = loop_time
#         self.skip_if_missed = skip_if_missed
#         self.running = False
#         self.wait = threading.Event()

#     def set_target(self, target: float):
#         self.target = target

#     def run(self, target: float):
#         self.set_target(target)
#         self.running = True
#         self.wait.clear()
#         while self.running:
#             self.loop()
#             if self.loop_time > 0:
#                 time_passed = time.monotonic() - self.last_loop_start_time
#                 sleep_time = self.loop_time - time_passed
#                 if (self.skip_if_missed) and sleep_time < 0:
#                     sleep_time = sleep_time % self.loop_time
#                 if sleep_time > 0 and self.running:
#                     self.wait.wait(timeout=sleep_time)

#     def loop(self):
#         self.last_loop_start_time = time.monotonic()
#         reading = self.sensor.read_with_time(self.last_loop_start_time)
#         self.control_law.write_state(reading.time, reading.value)
#         effort_time = time.monotonic()
#         effort = self.control_law.read_effort(self.target, effort_time)
#         self.actuator.write(effort)

#     def stop(self):
#         self.running = False
#         self.wait.set()


# class IndependentController:
#     sensor: Sensor
#     actuator: Actuator1D
#     control_law: ControlLaw

#     sensor_loop_time: float = 0
#     actuator_loop_time: float = 0
#     interrupt_with_new_target: bool = False

#     sensor_wait: threading.Event
#     actuator_wait: threading.Event

#     sensor_last_start_time: float = 0
#     actuator_last_start_time: float = 0
#     target: float
#     running: bool

#     def __init__(
#         self,
#         sensor: Sensor,
#         actuator: Actuator1D,
#         control_law: ControlLaw,
#         sensor_loop_time: float = 0,
#         actuator_loop_time: float = 0,
#         interrupt_with_new_target: bool = False,
#     ):
#         self.sensor = sensor
#         self.actuator = actuator
#         self.control_law = control_law
#         self.sensor_loop_time = sensor_loop_time
#         self.actuator_loop_time = actuator_loop_time
#         self.interrupt_with_new_target = interrupt_with_new_target
#         self.running = False
#         self.sensor_wait = threading.Event()
#         self.actuator_wait = threading.Event()

#     def run(self, target: float):
#         self.set_target(target)
#         self.running = True
#         self.sensor_wait.clear()
#         self.actuator_wait.clear()
#         sensor_thread = threading.Thread(target=self.sensor_loop, daemon=True)
#         actuator_thread = threading.Thread(target=self.actuator_loop, daemon=True)
#         sensor_thread.start()
#         actuator_thread.start()
#         sensor_thread.join()
#         actuator_thread.join()

#     def set_target(self, target: float):
#         self.target = target
#         if self.interrupt_with_new_target:
#             self.actuator_wait.set()

#     def sensor_loop(self):
#         while self.running:
#             self.sensor_update()
#             if self.sensor_loop_time > 0:
#                 time_passed = time.monotonic() - self.sensor_last_start_time
#                 sleep_time = self.sensor_loop_time - time_passed
#                 if sleep_time > 0 and self.running:
#                     self.sensor_wait.wait(timeout=sleep_time)

#     def actuator_loop(self):
#         while self.running:
#             self.actuator_wait.clear()
#             self.actuator_update()
#             if self.actuator_loop_time > 0:
#                 time_passed = time.monotonic() - self.actuator_last_start_time
#                 sleep_time = self.actuator_loop_time - time_passed
#                 if sleep_time > 0 and self.running:
#                     self.actuator_wait.wait(timeout=sleep_time)

#     def sensor_update(self):
#         self.sensor_last_start_time = time.monotonic()
#         reading = self.sensor.read_with_time(self.sensor_last_start_time)
#         self.control_law.write_state(reading.time, reading.value)

#     def actuator_update(self):
#         self.actuator_last_start_time = time.monotonic()
#         effort = self.control_law.read_effort(
#             self.target, self.actuator_last_start_time
#         )
#         self.actuator.write(effort)

#     def stop(self):
#         self.running = False
#         self.sensor_wait.set()
#         self.actuator_wait.set()


# class HumController:
#     sensor: Sensor
#     actuator: Actuator1D
#     control_law: ControlLaw

#     loop_time: float = 0
#     interrupt_with_new_target: bool = False

#     wait: threading.Event

#     last_start_time: float = 0
#     target: float
#     running: bool

#     def __init__(
#         self,
#         readings_queue: Queue[SensorReading],
#         actuator: Actuator1D,
#         control_law: ControlLaw,
#         loop_time: float = 0,
#         interrupt_with_new_target: bool = False,
#     ):
#         self.readings_queue = readings_queue
#         self.actuator = actuator
#         self.control_law = control_law
#         self.loop_time = loop_time
#         self.interrupt_with_new_target = interrupt_with_new_target
#         self.running = False
#         self.sensor_wait = threading.Event()
#         self.actuator_wait = threading.Event()

#     def set_target(self, target: float):
#         self.target = target
#         if self.interrupt_with_new_target:
#             self.wait.set()

#     def run(self, target: float):
#         self.set_target(target)
#         self.running = True
#         self.wait.clear()
#         while self.running:
#             self.wait.clear()
#             self.loop()
#             if self.loop_time > 0:
#                 time_passed = time.monotonic() - self.last_start_time
#                 sleep_time = self.loop_time - time_passed
#                 if sleep_time > 0 and self.running:
#                     self.wait.wait(timeout=sleep_time)

#     def loop(self):
#         self.last_start_time = time.monotonic()
#         readings: list[SensorReading] = []
#         while (reading := self.readings_queue.get_nowait()) is not None:
#             readings.append(reading)
#         effort = self.control_law.step(self.last_start_time, self.target, readings)
#         self.actuator.write(effort)

#     def stop(self):
#         self.running = False
#         self.wait.set()

# class HumidityTemperatureSensorFusion(Protocol):


class HumCtrlError(Exception): ...


class FlowExceedsMaxFullRangeError(HumCtrlError):
    def __init__(self, flow: float, max_flow: float, units: Optional[str] = None):
        self.flow = flow
        self.max_flow = max_flow
        self.units = units
        super().__init__(
            f"Flow {format_quantity(flow, units=units)} exceeds maximum full range flow {format_quantity(max_flow, units=units)} in humidity control mode"
        )


# class FlowMode:
#     def verify(self): ...
#     @property
#     def flow(self) -> Flow: ...


# @dataclass(frozen=True, slots=True)
# class BlendMaxFlow(FlowMode):
#     def flow(self) -> Flow:
#         return Flow.blend_max()


# @dataclass(frozen=True, slots=True)
# class AbsoluteFlow(FlowMode):
#     value: float

#     @property
#     def flow(self) -> Flow:
#         return Flow.absolute(self.value)


# @dataclass(frozen=True, slots=True)
# class FullRangeMaxFlow(FlowMode):
#     throttle: float = 1.0

#     def verify(self):
#         validate_normalised("FullRangeMaxFlow.throttle", self.throttle)

#     @property
#     def flow(self) -> Flow:
#         return Flow.full_range_max(self.throttle)


class PumpHumManager:
    controller: ControlLaw
    pumps: DualPumps
    recorder: HTRecorder
    process_sensor: HTSensors
    sensors: dict[str, HTSensor]
    wait: threading.Event
    processor: Any

    running: bool
    recorder_running: bool
    regulating: bool

    fraction_change_policy: FractionChangePolicy = FractionChangePolicy.HOLD_CLAMPED
    flow_change_policy: RaiseOrClamp = RaiseOrClamp.CLAMP

    time_offset: float

    loop_time: float
    regulating: bool
    target_humidity: float

    clock: Clock

    def __init__(
        self,
        *args,
        pumps: DualPumps,
    ):
        self.pumps = pumps

    def stop_pumps(self):
        self.regulating = False
        self.pumps.stop()

    def set_controller(self, controller: Any):
        self.controller = controller

    def set_flows(self, wet_flow: float, dry_flow: float):
        self.regulating = False
        self.pumps.set_flows(wet_flow, dry_flow)

    def set_blend(self, wet_fraction: float, flow: Flow):
        self.regulating = False
        self.pumps.set_blend(wet_fraction, flow, policy=self.fraction_change_policy)

    def set_flow(self, flow: Flow, policy: RaiseOrClamp | None = None):
        self.pumps.set_flow(flow, policy=policy or self.flow_change_policy)

    def set_wet_fraction(
        self, wet_fraction: float, policy: FractionChangePolicy | None = None
    ):
        self.pumps.set_wet_fraction(
            wet_fraction, policy=policy or self.fraction_change_policy
        )

    def stop(self):
        self.recorder_running = False
        self.regulating = False
        self.running = False
        self.wait.set()
        self.stop_pumps()

    def run(self):
        while self.running:
            start_time = time.monotonic()
            self.controller_loop()
            elapsed_time = time.monotonic() - start_time
            sleep_time = self.loop_time - elapsed_time
            if sleep_time > 0:
                self.wait.wait(timeout=sleep_time)

    def verify_flow_is_within_max_full_range(self, flow: float):
        if flow > self.pumps.max_full_range_flow:
            raise FlowExceedsMaxFullRangeError(
                flow, self.pumps.max_full_range_flow, self.pumps.flow_units
            )

    def regulate(self, humidity: float):
        self.target_humidity = humidity
        self.regulating = True

    def stop_regulating(self):
        self.regulating = False

    def controller_loop(self):
        reading = self.process_sensor.read()
        record: Record = Record.from_process_reading(reading)
        if self.regulating:
            record.target_humidity = self.target_humidity
            wet_fraction = self.controller.step(
                reading.time, reading.humidity, self.target_humidity
            )
            self.pumps.set_wet_fraction(wet_fraction, self.fraction_change_policy)

        record.set_flows(self.pumps.flows)
        if self.recorder_running:
            self.recorder.record(record)
