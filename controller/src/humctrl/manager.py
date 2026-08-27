from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from threading import Event, RLock, Thread
from typing import Any

from humctrl.clock import Clock, Duration, Time
from humctrl.cmds import Command, ControlProgram, HoldConfig, Runner, StopPumps
from humctrl.control_law import ControlLaw, ControlLawConfig
from humctrl.pumps import (
    AbsoluteFlows,
    Blend,
    BlendFlow,
    DualPumps,
    FractionChangePolicy,
    MaxFlows,
    OnOverdrive,
)
from humctrl.recorder import Recorder
from humctrl.sensors import (
    Reader,
    Reading,
    SensorReadError,
)
from humctrl.state import State
from humctrl.typing import NonNegative, Normalised, Percent, Positive
from humctrl.utils import PeriodicLoop, Topic, require

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


def expected_humidity_from_fraction(
    wet_humidity: Percent, dry_humidity: Percent, wet_fraction: Normalised
) -> Percent:
    return dry_humidity + wet_fraction * (wet_humidity - dry_humidity)


def get_expected_humidity_from_flows(
    wet_humidity: Percent, dry_humidity: Percent, wet_flow: NonNegative, dry_flow: NonNegative
) -> Percent:
    return (wet_flow * wet_humidity + dry_flow * dry_humidity) / (wet_flow + dry_flow)


def calculate_fraction(
    wet_humidity: Percent, dry_humidity: Percent, target: Percent
) -> Normalised | Overdriven:

    if wet_humidity <= dry_humidity:
        raise PumpHumiditiesError()
    if target < dry_humidity:
        return Overdriven.DRY
    if target > wet_humidity:
        return Overdriven.WET
    return (target - dry_humidity) / (wet_humidity - dry_humidity)


class Regulator:
    pumps: DualPumps
    controller: ControlLaw
    target: Percent

    def __init__(
        self,
        controller: ControlLaw,
        reading: Reading,
        target: Percent,
        output: float | None = None,
    ):
        self.controller = controller
        self.target = target
        self.controller.start(reading.time, reading.humidity, target, output=output)

    def update_target(self, target: Percent):
        self.target = target

    def __call__(self, reading: Reading) -> float:
        return self.controller.step(reading.time, reading.humidity, self.target)


@dataclass(slots=True, frozen=True)
class Msg:
    time: Time


@dataclass(slots=True, frozen=True)
class FlowsMsg(Msg):
    flows: AbsoluteFlows | None


@dataclass(slots=True, frozen=True)
class HumidityMsg(Msg):
    humidity: Percent | None


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
class Lines[T: float]:
    wet: T
    dry: T


class Setup:
    loop_time: Duration
    flow_humidities: Lines[Percent]
    max_flows: MaxFlows | None


class Manager:
    _pumps: DualPumps | None = None
    _control_law: ControlLaw | None = None
    _recorder: Recorder | None = None
    _process_reader: Reader | None = None

    _clock: Clock
    _wait: Event = Event()
    lock: RLock = RLock()
    _process_thread: Thread | None = None

    _recording: bool = False
    _process_time: Positive

    _fraction_change_policy: FractionChangePolicy = FractionChangePolicy.HOLD_CLAMPED
    _flow_change_policy: OnOverdrive = OnOverdrive.CLAMP

    _target_wet_fraction: Normalised | None = None
    _target_total_flow: BlendFlow = BlendFlow.absolute(0.0)

    _fall_back_mode: Command = StopPumps()

    _program: ControlProgram | None = None  #
    program_running: bool = False
    _runner: Runner | None = None

    _line_humidities: Lines[Percent]
    _total_flow_humidity: Percent

    _state_topic: Topic[State]

    _process_reading_topic: Topic[Reading]
    _flows_topic: Topic[FlowsMsg]
    _target_humidity_topic: Topic[HumidityMsg]

    _warnings_topic: Topic[Any]

    _cached_state: State

    _setup: Setup

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
        self._dry_humidity = dry_humidity
        self._wet_humidity = wet_humidity

        self._wait = Event()
        self._clock = clock or Clock()
        self._update_time_ns = self._clock.now_ns()
        self._last_sensor_readings = {}
        self._main_thread = PeriodicLoop(self.main_step, process_time)

        self._process_reading_topic = Topic[Reading]()
        self._state_topic = Topic[State]()
        self._warnings_topic = Topic[Any]()

        self.read_state()

    # region Properties
    @property
    def running(self) -> bool:
        return self._main_thread.running

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def flows(self) -> AbsoluteFlows | None:
        return self._flows

    @property
    def blend(self) -> Blend:
        return self.require_pumps().blend

    @property
    def wet_fraction(self) -> Normalised:
        return self.require_pumps().wet_fraction

    @property
    def dry_fraction(self) -> Normalised:
        return self.require_pumps().dry_fraction

    @property
    def fraction_change_policy(self) -> FractionChangePolicy:
        return self._fraction_change_policy

    @property
    def required_target_wet_fraction(self) -> Normalised:
        return require(self._target_wet_fraction, TargetHumidityNotSetError)

    @property
    def target_humidity(self) -> Percent | None:
        return self._regulator and self._regulator.target

    @property
    def required_target_humidity(self) -> Percent:
        return require(self._regulator, RegulatorNotRunningError).target

    @property
    def required_process_reading(self) -> Reading:
        return require(self._cached_state.process_reading, ProcessReadingNotAvailableError)

    @property
    def required_process_humidity(self) -> Percent:
        return self.required_process_reading.humidity

    @property
    def process_reading(self) -> Reading | None:
        return self._cached_state.process_reading

    @property
    def process_humidity(self) -> Percent | None:
        reading = self.process_reading
        if reading is not None:
            return reading.humidity

    @property
    def dry_humidity(self) -> Percent:
        return self._dry_humidity

    @property
    def wet_humidity(self) -> Percent:
        return self._wet_humidity

    @property
    def total_flow_humidity(self) -> Percent:
        return self.get_humidity_of_flow(self.wet_fraction)

    def calculate_wet_fraction(self, target: Percent) -> Normalised | Overdriven:
        return calculate_fraction(self.wet_humidity, self.dry_humidity, target)

    def get_humidity_of_flow(self, wet_fraction: Normalised) -> Percent:
        return expected_humidity_from_fraction(self.wet_humidity, self.dry_humidity, wet_fraction)

    def time_ns(self) -> int:
        return self._clock.now_ns()

    def time(self) -> float:
        return self._clock.now()

    def get_fraction_change_policy(
        self, policy: FractionChangePolicy | None
    ) -> FractionChangePolicy:
        return policy or self._fraction_change_policy

    # endregion

    # region Setter

    def set_pumps(self, pumps: DualPumps):
        self._pumps = pumps
        self.update_flows(pumps.flows, publish_state=False)

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

    def require_regulator(self) -> Regulator:
        if self._regulator is None:
            raise RegulatorNotRunningError()
        return self._regulator

    def require_process_reader(self) -> Reader:
        return require(self._process_reader, ProcessSensorNotSetError)

    def require_pumps(self) -> DualPumps:
        if self._pumps is None:
            self._flows = None
            raise PumpsNotSetError()
        return self._pumps

    def on_overdrive(self, on_overdrive: OnOverdrive | None = None) -> OnOverdrive:
        return on_overdrive or self._flow_change_policy

    def update_flows[T: AbsoluteFlows | None](self, flows: T, publish_state: bool = True) -> T:
        time = self._clock.time()
        self._cached_state.update_flows(flows, time=time)
        self.publish_flows(time)
        if publish_state:
            self.publish_state()
        return flows

    def update_target_humidity(self, target_humidity: Percent | None, publish_state: bool = True):
        time = self._clock.time()
        self._cached_state.set_target_humidity(target_humidity, time=time)
        self.publish_target_humidity(time=time)
        if publish_state:
            self.publish_state()

    def read_state(self) -> State:
        state = State(time=self._clock.time())
        if self._process_reader:
            state.update_process_reading(self._process_reader.read())
        if self._pumps:
            state.update_flows(self._pumps.flows)
        if self._regulator:
            state.set_target_humidity(self._regulator.target)
        self._cached_state = state
        return self._cached_state

    def record_state(self):
        self.require_recorder().record_state(self._cached_state)

    # region Publishers

    def publish_flows(self, time: Time | None = None):
        self._flows_topic.publish(FlowsMsg(time or self._clock.time(), self._cached_state.flows))

    def publish_target_humidity(self, time: Time | None = None):
        self._target_humidity_topic.publish(
            HumidityMsg(time or self._clock.time(), self.target_humidity)
        )

    def publish_process_reading(self):
        self._process_reading_topic.publish(self.required_process_reading)

    def publish_state(self):
        self._state_topic.publish(self._cached_state)

    def publish_warning(self, error: Exception):
        self._warnings_topic.publish(ErrorMsg(self._clock.time(), str(error)))

    # endregion

    # region Pumps control

    @contextmanager
    def manual_pumps(self) -> Generator[DualPumps]:
        """Take the loop off the pumps and hand them over, under the lock."""
        with self.lock:
            pumps = self.require_pumps()
            self.set_regulating_stopped()
            yield pumps

    def on_pump_error(self, error: Exception):
        self.update_flows(None)
        self._warnings_topic.publish(ErrorMsg(self._clock.time(), str(error)))

    def update_target_blend(self, wet: NonNegative, dry: NonNegative):
        self._target_wet_fraction = wet / (wet + dry) if wet + dry > 0 else None
        self._target_total_flow = BlendFlow.absolute(wet + dry)

    def set_flows(self, wet: NonNegative, dry: NonNegative) -> AbsoluteFlows:
        with self.manual_pumps() as pumps:
            self.update_target_blend(wet, dry)
            self.update_flows(pumps.set_flows(wet, dry))
            try:
                return self.update_flows(pumps.set_flows(wet, dry))
            except Exception as e:
                self.on_pump_error(e)
                raise e

    def set_efforts(self, wet: Normalised, dry: Normalised) -> AbsoluteFlows:
        with self.manual_pumps() as pumps:
            flows = pumps.efforts_to_flows(pumps.set_efforts(wet, dry))
            self.update_target_blend(flows.wet, flows.dry)
            try:
                return self.update_flows(pumps.efforts_to_flows(pumps.set_efforts(wet, dry)))
            except Exception as e:
                self.on_pump_error(e)
                raise e

    def set_blend(
        self,
        flow: BlendFlow | Positive,
        wet_fraction: Normalised,
        on_overdrive: OnOverdrive | None = None,
    ) -> AbsoluteFlows:
        return self._set_blend(flow, wet_fraction, on_overdrive=on_overdrive)

    def _set_blend(
        self,
        flow: BlendFlow | Positive | None = None,
        wet_fraction: Normalised | None = None,
        on_overdrive: OnOverdrive | None = None,
        publish_state: bool = True,
    ) -> AbsoluteFlows:
        with self.manual_pumps() as pumps:
            if wet_fraction is not None:
                self._target_wet_fraction = wet_fraction
            if flow is not None:
                self._target_total_flow = (
                    flow if isinstance(flow, BlendFlow) else BlendFlow.absolute(flow)
                )
            try:
                return self.update_flows(
                    pumps.set_blend(
                        self._target_total_flow,
                        self.required_target_wet_fraction,
                        on_overdrive=self.on_overdrive(on_overdrive),
                    ),
                    publish_state=publish_state,
                )
            except Exception as e:
                self.on_pump_error(e)
                raise e

    def set_blend_flow(
        self, flow: BlendFlow | Positive, on_overdrive: OnOverdrive | None = None
    ) -> AbsoluteFlows:
        return self._set_blend(flow, None, on_overdrive=on_overdrive)

    def set_wet_fraction(
        self,
        fraction: Normalised,
        on_overdrive: OnOverdrive | None = None,
    ) -> AbsoluteFlows:
        return self._set_blend(None, fraction, on_overdrive)

    def set_dry_fraction(
        self, fraction: Normalised, on_overdrive: OnOverdrive | None = None
    ) -> AbsoluteFlows:
        return self.set_wet_fraction(1.0 - fraction, on_overdrive=on_overdrive)

    def stop_pumps(self):
        with self.manual_pumps() as pumps:
            self.update_target_blend(0.0, 0.0)
            try:
                pumps.stop()
                self.update_flows(pumps.flows)
            except Exception as e:
                self.on_pump_error(e)
                raise e

    # endregion

    def start_regulating(self, humidity: Percent):
        with self.lock:
            self.verify_running()
            self.require_pumps()
            self._regulator = Regulator(
                self.require_control_law(),
                self.required_process_reading,
                humidity,
                output=float(self.calculate_wet_fraction(humidity)),
            )
            self.update_target_humidity(humidity)

    def update_regulating(self, humidity: Percent):
        with self.lock:
            self.require_regulator().update_target(humidity)
            self.update_target_humidity(humidity)

    def set_regulating_stopped(self):
        with self.lock:
            self._regulator = None
            self.update_target_humidity(None)

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

    def _read_process(self) -> Reading:
        try:
            reading = self.require_process_reader().read()
        except SensorReadError as e:
            raise HumCtrlError(e) from e
        with self.lock:
            self._cached_state.update_process_reading(reading)
            self._update_time_ns = reading.time_ns
            if self.recording:
                self.record_state()
        return reading

    def read_process(self) -> Reading:
        reading = self._read_process()
        self.publish_state()
        return reading

    def main_step(self):
        reading = self.read_process()
        with self.lock:
            if self._runner is not None:
                self._runner.step(reading)
            if self._regulator is not None:
                self._set_blend(None, self._regulator(reading), publish_state=False)
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


class RegulateHumidityConfig:
    humidity: Percent
    hold: Duration | HoldConfig | Time | None


class RampHumidityConfig:
    target: Percent
    duration: Duration
    hold: Duration | HoldConfig | Time | None


DEFAULT_BLEND_FLOW: BlendFlow = BlendFlow.full_range_max()


class Regulation:
    def __init__(
        self,
        manager: Manager,
        steps: list[RegulateHumidityConfig | RampHumidityConfig],
        flow: BlendFlow | Positive | None = None,
        policy: FractionChangePolicy | None = None,
    ):
        self.manager = manager
        self.steps = steps
        self.flow = flow
        self.policy = policy
