from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from threading import Event, RLock, Thread
from typing import Any, Protocol

from humctrl.blender import (
    HumidityRailError,
    Rail,
    calculate_wet_fraction,
    expected_humidity_from_fraction,
)
from humctrl.clock import Clock, Duration, Time
from humctrl.cmds import Command, ControlProgram, Runner, StopPumps
from humctrl.control_law import ControlLaw, ControlLawConfig
from humctrl.pumps import (
    AbsoluteFlows,
    Blend,
    BlendFlow,
    DualPumps,
    Efforts,
    MaxFlows,
    MaxFullRangeMax,
    PumpsOutput,
)
from humctrl.recorder import Recorder
from humctrl.sensors import (
    Reader,
    Readers,
    Reading,
)
from humctrl.state import State
from humctrl.typing import NonNegative, Normalised, Percent, Positive
from humctrl.utils import PeriodicLoop, Topic, Unset, UnsetType, require

# region Exceptions


class HumCtrlError(Exception): ...


class RecorderNotSetError(HumCtrlError):
    def __init__(self):
        super().__init__(
            "Recorder not set. Use set_recorder() to set a recorder before starting recording."
        )


class ManagerNotRunningError(HumCtrlError):
    def __init__(self):
        super().__init__(
            "Manager not running. Use start() to start the manager before calling this method."
        )


class ControlLawNotSetError(HumCtrlError):
    def __init__(self):
        super().__init__(
            "Control law not set. Use set_control_law() to set a control law before starting "
            "regulation."
        )


class ProcessSensorNotSetError(HumCtrlError):
    def __init__(self):
        super().__init__(
            "Process sensor not set. Use set_process_sensor() to set a process sensor before "
            "reading process data."
        )


class TargetHumidityNotSetError(HumCtrlError):
    def __init__(self):
        super().__init__(
            "Target humidity not set. Use start_regulating() to set a target humidity before "
            "reading regulated humidity."
        )


class TargetStreamNotSetError(HumCtrlError):
    def __init__(self):
        super().__init__(
            "Target stream not set. Use start_stream() to set a target stream before "
            "reading regulated humidity."
        )


class RegulatorNotRunningError(HumCtrlError):
    def __init__(self):
        super().__init__(
            "Regulator not running. Use start_regulation() to start the regulator before calling "
            "this method."
        )


class CurrentWetFractionNotSetError(HumCtrlError):
    def __init__(self):
        super().__init__(
            "Current wet fraction not set. Use set_flows() or set_blend() to set the current wet "
            "fraction before calling this method."
        )


class PumpsNotSetError(HumCtrlError):
    def __init__(self):
        super().__init__("Pumps not set. Use set_pumps() to set pumps before using them.")


class ProcessReadingNotAvailableError(HumCtrlError):
    def __init__(self):
        super().__init__(
            "Process reading not available. Use read_process() to read process data before "
            "accessing it."
        )


class PumpHumidityNotSetError(HumCtrlError):
    def __init__(self, line: str):
        super().__init__(f"Pump humidities for '{line}' pump not set.")


class PumpHumiditiesError(HumCtrlError):
    def __init__(self):
        super().__init__(
            "Wet and dry humidities are not suitable. Cannot calculate wet fraction for regulation."
        )


class CommandAlreadyRunningError(HumCtrlError):
    def __init__(self, current: Runner, new: Command):
        super().__init__(
            f"Command is already running ({current}). Interrupt it before starting a new one "
            f"({new})."
        )


class ProgramAlreadyRunningError(HumCtrlError):
    def __init__(self):
        super().__init__("Program is already running. Interrupt it before starting a new ones")


# endregion


class Overdriven(Enum):
    WET = "wet"
    DRY = "dry"

    def __float__(self) -> float:
        match self:
            case Overdriven.WET:
                return 1.0
            case Overdriven.DRY:
                return 0.0


# class Regulator:
#     pumps: DualPumps
#     controller: ControlLaw
#     target: Percent
#     wet_humidity: Percent
#     dry_humidity: Percent

#     def __init__(
#         self,
#         controller: ControlLaw,
#         reading: Reading,
#         dry_humidity: Percent,
#         wet_humidity: Percent,
#         target: Percent,
#     ):
#         self.controller = controller
#         self.dry_humidity = dry_humidity
#         self.wet_humidity = wet_humidity
#         self.target = target
#         self.controller.start(reading.time, reading.humidity, target, target)

#     def calculate_wet_fraction(self, target: Percent) -> Normalised | Rail:
#         return calculate_wet_fraction(self.dry_humidity, self.wet_humidity, target)

#     def update_humidities(
#         self, dry: Percent | None = None, wet: Percent | None = None
#     ) -> Normalised | Rail:
#         if wet is not None:
#             self.wet_humidity = wet
#         if dry is not None:
#             self.dry_humidity = dry
#         return self.calculate_wet_fraction(self.target)

#     def update_target(self, target: Percent):
#         self.target = target

#     def step(self, reading: Reading, last_applied: Percent) -> Normalised | Rail:
#         self.controller.step(reading.time, reading.humidity, self.target, last_applied=last_applied)
#         return self.calculate_wet_fraction(self.output)


@dataclass(slots=True, frozen=True)
class Msg:
    time: Time


@dataclass(slots=True, frozen=True, init=False)
class PumpsOutputMsg(Msg):
    efforts: Efforts | None = None
    flows: AbsoluteFlows | None = None
    humidity: Percent | None = None

    def __init__(
        self, time: Time, pumps_output: PumpsOutput | None = None, humidity: Percent | None = None
    ):
        super().__init__(time)
        if pumps_output is not None:
            object.__setattr__(self, "efforts", pumps_output.efforts)
            object.__setattr__(self, "flows", pumps_output.flows)
        object.__setattr__(self, "humidity", humidity)


@dataclass(slots=True, frozen=True)
class HumidityMsg(Msg):
    humidity: Percent | None

@dataclass(slots=True, frozen=True)
class ReadingsMsg(Msg):
    process: Reading | None = None
    dry: Reading | None = None
    wet: Reading | None = None

@dataclass(slots=True, frozen=True)
class ErrorMsg(Msg):
    error: str


# @dataclass(slots=True)
# class Controlled:
#     _blend_flow: bool = False
#     _fraction: bool = False

#     def __call__(
#         self,
#         target_humidity: bool | None = None,
#         fraction: bool | None = None,
#         blend_flow: bool | None = None,
#         flows: bool | None = None,
#     ):

#         if fraction is not None:
#             self._fraction = fraction
#         if blend_flow is not None:
#             self._blend_flow = blend_flow

#     @property
#     def target_humidity(self) -> bool:
#         return self._target_humidity or self._flows or self._fraction

#     @property
#     def fraction(self) -> bool:
#         return self._fraction or self._flows or self._target_humidity

#     @property
#     def blend_flow(self) -> bool:
#         return self._blend_flow or self._flows

#     @property
#     def flows(self) -> bool:
#         return self._flows or self._blend_flow or self._fraction or self._target_humidity


@dataclass(slots=True, frozen=True)
class Lines[T]:
    wet: T
    dry: T


@dataclass(slots=True)
class Stream:
    humidity: Percent
    flow: BlendFlow = MaxFullRangeMax

    def update_stream(
        self, humidity: Percent | UnsetType = Unset, flow: BlendFlow | UnsetType = Unset
    ):
        if humidity is not Unset:
            self.humidity = humidity
        if flow is not Unset:
            self.flow = flow


class SystemErrors(Protocol):
    time: Time
    pumps: UnsetType | Exception | None
    stream: UnsetType | Exception | None
    regulator: UnsetType | Exception | None


class Manager:
    _pumps: DualPumps | None = None
    _control_law: ControlLaw | None = None
    _recorder: Recorder | None = None
    _readers: Readers | None = None

    _clock: Clock
    lock: RLock = RLock()
    _process_thread: Thread | None = None

    _recording: bool = False
    _process_time: Positive

    _fall_back_mode: Command = StopPumps()
    _dry_humidity_fallback: Percent | None = None
    _wet_humidity_fallback: Percent | None = None

    _program: ControlProgram | None = None
    program_running: bool = False
    _runner: Runner | None = None

    _target_stream: Stream | None = None
    _regulated_humidity: Percent | None = None

    _state_topic: Topic[State]

    _readings_topic: Topic[ReadingsMsg]
    _pumps_topic: Topic[PumpsOutputMsg]
    _regulated_humidity_topic: Topic[HumidityMsg]
    _warnings_topic: Topic[Any]

    # Cached state
    _pump_outputs: PumpsOutput | None = None
    process_reading: Reading | None = None
    dry_reading: Reading | None = None
    wet_reading: Reading | None = None
    flow_humidity: Percent | None = None


    def __init__(
        self,
        pumps: DualPumps | None = None,
        process_time: float = 1.0,
        control_law: ControlLaw | ControlLawConfig | None = None,
        recorder: Recorder | None = None,
        process_reader: Reader | None = None,
        clock: Clock | None = None,
        dry_humidity: float = 0,
        wet_humidity: float = 100,
    ):
        self._pumps = pumps
        if control_law is not None:
            self.set_control_law(control_law)

        self._recorder = recorder
        self._process_reader = process_reader
        self._dry_humidity_fallback = dry_humidity
        self._wet_humidity_fallback = wet_humidity

        self._clock = clock or Clock()
        self._update_time_ns = self._clock.now_ns()
        self._last_sensor_readings = {}
        self._main_thread = PeriodicLoop(self.main_step, process_time)

        self._process_reading_topic = Topic[Reading]()
        self._state_topic = Topic[State]()
        self._warnings_topic = Topic[Any]()


    # region Properties
    @property
    def running(self) -> bool:
        return self._main_thread.running

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def regulated_humidity(self) -> Percent | None:
        return self._regulated_humidity

    @property
    def required_regulated_humidity(self) -> Percent:
        return require(self._regulator, RegulatorNotRunningError).target

    @property
    def required_process_reading(self) -> Reading:
        return require(self.process_reading, ProcessReadingNotAvailableError)

    @property
    def process_humidity(self) -> Percent | None:
        reading = self.process_reading
        if reading is not None:
            return reading.humidity

    @property
    def required_process_humidity(self) -> Percent:
        return self.required_process_reading.humidity

    @property
    def dry_reading_humidity(self) -> Percent | None:
        reading = self.dry_reading
        if reading is not None:
            return reading.humidity

    @property
    def wet_reading_humidity(self) -> Percent | None:
        reading = self.wet_reading
        if reading is not None:
            return reading.humidity

    @property
    def dry_humidity(self) -> Percent | None:
        reading = self.dry_reading_humidity
        return reading if reading is not None else self._dry_humidity_fallback

    @property
    def wet_humidity(self) -> Percent | None:
        reading = self.wet_reading_humidity
        return reading if reading is not None else self._wet_humidity_fallback

    @property
    def required_dry_humidity(self) -> Percent:
        return require(self.dry_humidity, PumpHumidityNotSetError, "dry")

    @property
    def required_wet_humidity(self) -> Percent:
        return require(self.wet_humidity, PumpHumidityNotSetError, "wet")

    @property
    def required_stream(self) -> Stream:
        return require(self._target_stream, TargetHumidityNotSetError)

    @property
    def pump_outputs(self) -> PumpsOutput | None:
        return self._pump_outputs

    @property
    def required_pumps_output(self) -> PumpsOutput:
        return require(self._pump_outputs, PumpsNotSetError)

    @property
    def flows(self) -> AbsoluteFlows | None:
        return self._pump_outputs.flows if self._pump_outputs is not None else None

    @property
    def target_stream(self) -> Stream | None:
        return self._target_stream

    @property
    def required_target_stream(self) -> Stream:
        return require(self._target_stream, TargetHumidityNotSetError)

    @property
    def state(self) -> State:
        return State(
            time=self._clock.time(),
            process_reading=self.process_reading,
            dry_reading=self.dry_reading,
            wet_reading=self.wet_reading,
            pumps=self._pump_outputs,
            target_humidity=self._regulated_humidity,
            flow_humidity=self.flow_humidity,
        )

    def calculate_wet_fraction(self, target: Percent) -> Normalised | Rail:
        return calculate_wet_fraction(
            self.required_dry_humidity, self.required_wet_humidity, target
        )

    def calculate_expected_humidity(self, wet_fraction: Normalised) -> Percent | None:
        dry_humidity, wet_humidity = self.dry_humidity, self.wet_humidity
        if dry_humidity is not None and wet_humidity is not None:
            return expected_humidity_from_fraction(dry_humidity, wet_humidity, wet_fraction)

    def get_humidity_of_flow(self, wet_fraction: Normalised) -> Percent | None:
        dry_humidity, wet_humidity = self.dry_humidity, self.wet_humidity
        if dry_humidity is not None and wet_humidity is not None:
            return expected_humidity_from_fraction(dry_humidity, wet_humidity, wet_fraction)

    def time_ns(self) -> int:
        return self._clock.now_ns()

    def time(self) -> float:
        return self._clock.now()

    # endregion

    # region Setter

    def set_pumps(self, pumps: DualPumps):
        self._pumps = pumps

    def set_recorder(self, recorder: Recorder):
        self._recorder = recorder

    def set_process_reader(self, reader: Reader):
        self._process_reader = reader
        self.read_process()

    def set_control_law(self, control_law: ControlLaw | ControlLawConfig):
        self.set_regulating_stopped()
        self._control_law = (
            control_law.build() if isinstance(control_law, ControlLawConfig) else control_law
        )

    # endregion

    def verify_running(self):
        if not self.running:
            raise ManagerNotRunningError()

    def require_recorder(self) -> Recorder:
        return require(self._recorder, RecorderNotSetError)

    def require_control_law(self) -> ControlLaw:
        if self._control_law is None:
            self.set_regulating_stopped()
            raise ControlLawNotSetError()
        return self._control_law

    def require_pumps(self) -> DualPumps:
        if self._pumps is None:
            self._flows = None
            raise PumpsNotSetError()
        return self._pumps

    # def update_output(self, output: PumpsOutput):

    def update_cached_time(self, time: int | None = None):
        self.last_updated_ns = time if time is not None else self._clock.now_ns()

    def record_state(self):
        self.require_recorder().record_state(self.state)

    # region Publishers

    def publish_pumps(self):
        self._pumps_topic.publish(
            PumpsOutputMsg(
                self._clock.time(),
                self._pump_outputs,
                self.flow_humidity
            )
        )

    def publish_regulated_humidity(self):
        self._regulated_humidity_topic.publish(
            HumidityMsg(self._clock.time(), self.regulated_humidity)
        )

    def publish_process_reading(self):
        self._process_reading_topic.publish(self.required_process_reading)

    def publish_readings(self):
        self._readings_topic.publish(
            ReadingsMsg(
                time=self._clock.time(),
                process=self.process_reading,
                dry=self.dry_reading,
                wet=self.wet_reading
            )
        )

    def publish_state(self):
        self._state_topic.publish(self.state)

    def publish_warning(self, error: Exception):
        self._warnings_topic.publish(ErrorMsg(self._clock.time(), str(error)))

    def on_regulation_warning(self, error: Exception):
        self.update_regulated_humidity(None)
        self._warnings_topic.publish(ErrorMsg(self._clock.time(), str(error)))

    def on_pump_error(self, error: Exception):
        self._pump_outputs = None
        self.flow_humidity = None
        self.last_updated_ns = self._clock.now_ns()
        self._warnings_topic.publish(ErrorMsg(self._clock.time(), str(error)))

    # endregion

    # region Pumps control

    @contextmanager
    def manual_pumps(self) -> Generator[DualPumps]:
        """Take the loop off the pumps and hand them over, under the lock."""
        with self.lock:
            pumps = self.require_pumps()
            self.set_regulating_stopped()
            self._target_stream = None
            self._regulated_humidity = None
            yield pumps

    def set_flows(self, wet: NonNegative, dry: NonNegative) -> PumpsOutput:
        with self.manual_pumps() as pumps:
            try:
                return self.update_outputs(pumps.set_flows(wet, dry))
            except Exception as e:
                self.on_pump_error(e)
                raise e

    def set_efforts(self, wet: Normalised, dry: Normalised) -> PumpsOutput:
        with self.manual_pumps() as pumps:
            try:
                return self.update_outputs(pumps.set_efforts(wet, dry))
            except Exception as e:
                self.on_pump_error(e)
                raise e

    def stop_pumps(self):
        with self.manual_pumps() as pumps:
            try:
                pumps.stop()
                self.update_outputs(pumps.output)
            except Exception as e:
                self.on_pump_error(e)
                raise e

    def start_stream(self, flow: BlendFlow, humidity: Percent) -> PumpsOutput:
        with self.manual_pumps() as pumps:
            return self._start_stream(flow=flow, humidity=humidity, pumps=pumps)

    def _start_stream(
        self, flow: BlendFlow, humidity: Percent, pumps: DualPumps | None = None
    ) -> PumpsOutput:
        self._target_stream = Stream(humidity=humidity, flow=flow)
        return self._run_stream(self._target_stream, pumps=pumps)

    def _run_stream(
        self,
        /,
        stream: Stream | None = None,
        dry_humidity: Percent | None = None,
        wet_humidity: Percent | None = None,
        pumps: DualPumps | None = None,
    ) -> PumpsOutput:
        pumps = pumps if pumps is not None else self.require_pumps()
        stream = stream if stream is not None else self.required_target_stream
        dry_humidity = dry_humidity if dry_humidity is not None else self.required_dry_humidity
        wet_humidity = wet_humidity if wet_humidity is not None else self.required_wet_humidity
        wet_fraction = expected_humidity_from_fraction(dry_humidity, wet_humidity, stream.humidity)
        if isinstance(wet_fraction, Rail):
            error = HumidityRailError(dry_humidity, wet_humidity, stream.humidity)
            self._warnings_topic.publish(ErrorMsg(self._clock.time(), str(error)))
        return self.update_outputs(pumps.set_blend(stream.flow, float(wet_fraction)))

    # endregion

    def update_regulated_humidity(self, humidity: Percent | None, publish_state: bool = True):
        time = self._clock.time()
        self._regulated_humidity = humidity
        self.publish_regulated_humidity()
        if publish_state:
            self.publish_state()

    def start_regulating(self, humidity: Percent, flow: BlendFlow = MaxFullRangeMax):
        with self.lock:
            self.verify_running()
            pumps = self.require_pumps()
            self._regulated_humidity = humidity
            self.update_regulated_humidity(humidity, publish_state=False)
            self._start_stream(flow, humidity, pumps=pumps)

    def update_regulating(self, humidity: Percent):
        with self.lock:
            self.update_regulated_humidity(humidity)

    def set_regulating_stopped(self):
        with self.lock:
            self._regulator = None
            self.update_regulated_humidity(None)

    def stop_main(self):
        with self.lock:
            self.stop_recording()
            self._main_thread.stop()

    def stop(self):
        with self.lock:
            self.stop_main()
            self.stop_pumps()

    def start_recording(self, flag: str | None = None):
        with self.lock:
            self.verify_running()
            recorder = self.require_recorder()
            self._recording = True
            if flag is not None:
                recorder.add_flag(flag, self.time_ns())

    def stop_recording(self, flag: str | None = None):
        with self.lock:
            self._recording = False
            if flag is not None:
                self.require_recorder().add_flag(flag, self.time_ns())

    def add_recorder_flag(self, flag: str):
        with self.lock:
            self.require_recorder().add_flag(flag, self.time_ns())

    def start_process_reading(self):
        with self.lock:
            if not self.running:
                self._main_thread.start()

    # def _read_process(self) -> Reading:
    #     try:
    #         reading = self.require_process_reader().read()
    #     except SensorReadError as e:
    #         raise HumCtrlError(e) from e
    #     with self.lock:
    #         self._cached_state.update_process_reading(reading)
    #         self._update_time_ns = reading.time_ns
    #         if self.recording:
    #             self.record_state()
    #     return reading

    def read_process(self) -> Reading:
        reading = self._read_process()
        self.publish_state()
        return reading

    def update_readings(
        self,
        )

    def update_readings(
        self,
        process: Reading | UnsetType | None = Unset,
        dry: Reading | UnsetType | None = Unset,
        wet: Reading | UnsetType | None = Unset,
    ):
        if process is not Unset:
            self._process_reading = process
        if dry is not Unset:
            self._dry_reading = dry
        if wet is not Unset:
            self._wet_reading = wet
        
        self.update_readings

    def update_control(self):
        with self.lock:
            if isinstance(self._process_reading, Reading) and self._regulated_humidity is not None:
                self._regulation_step(self._process_reading, self._regulated_humidity)
            if self._target_stream is not None:
                try:
                    self._run_stream(self._target_stream)
                except Exception as e:
                    self.publish_warning(e)

    def update_outputs(
        self,
        outputs: PumpsOutput,
    ) -> PumpsOutput:
        time = self._clock.time()
        self._pump_outputs = outputs
        if outputs.flows.total > 0.0:
            self.flow_humidity = self.calculate_expected_humidity(outputs.flows.wet_fraction)
        else:
            self.flow_humidity = None
        self.publish_pumps(time)
        return outputs

    def _regulation_step(self, reading: Reading, target: Percent):
        try:
            control_law = self.require_control_law()
            stream_humidity = control_law.step(
                reading.time, reading.humidity, target, self.flow_humidity
            )
            self.required_target_stream.update_stream(humidity=stream_humidity)
        except Exception as e:
            self.publish_warning(e)

    def main_step(self):
        reading = self.read_process()
        with self.lock:
            if self._runner is not None:
                self._runner.step(reading)
            if self._regulated_humidity is not None:
                self._regulation_step(reading, self._regulated_humidity)
            self.publish_state()

    def _run_command(self, command: Command):
        if self._runner is not None:
            raise CommandAlreadyRunningError(self._runner, command)
        with self.lock:
            self._runner = command(self)
        if self._runner is not None:
            self._runner.hold()
            self._runner = None

    def interrupt(self):
        with self.lock:
            if self._runner is not None:
                self._runner.interrupt()
                self._runner = None

    def run_program(self, program: ControlProgram | list[Command]):
        with self.lock:
            if self._program is not None and self._program.running:
                raise HumCtrlError(
                    "A program is already running. Interrupt it before starting a new one."
                )
        if isinstance(program, list):
            program = ControlProgram(program)
        self._program = program
        self._program.run(self)

    def fallback(self):
        self.stop_recording()
        self.stop_main()
        match self._fall_back_mode:
            case StopPumps():
                self.stop_pumps()
            case AbsoluteFlows() as flows:
                self.set_flows(flows.wet, flows.dry)
            case Blend() as blend:
                self.set_blend(blend.wet_fraction, blend.flow)
            case None:
                pass


# class RegulateHumidityConfig:
#     humidity: Percent
#     hold: Duration | HoldConfig | Time | None


# class RampHumidityConfig:
#     target: Percent
#     duration: Duration
#     hold: Duration | HoldConfig | Time | None


# class Regulation:
#     def __init__(
#         self,
#         manager: Manager,
#         steps: list[RegulateHumidityConfig | RampHumidityConfig],
#         flow: BlendFlow | Positive | None = None,
#         policy: FractionChangePolicy | None = None,
#     ):
#         self.manager = manager
#         self.steps = steps
#         self.flow = flow
#         self.policy = policy
