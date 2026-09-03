from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from threading import RLock, Thread
from typing import Any, Protocol

from humctrl.clock import Clock, Duration, Time
from humctrl.cmds import Command, ControlProgram, Runner
from humctrl.controller import (
    OPEN_LOOP_LAW,
    ClosedLoop,
    ControlLaw,
    ControlLawConfig,
    Controller,
    OpenLoop,
    OpenLoopLaw,
    expected_humidity_from_fraction,
    get_expected_humidity_from_flows,
)
from humctrl.error import (
    CommandAlreadyRunningError,
    ControlLawNotSetError,
    ControllerAlreadyRunningError,
    ControllerNotRunningError,
    HumCtrlError,
    ManagerNotRunningError,
    ProcessReadingNotAvailableError,
    PumpHumidityNotSetError,
    PumpsNotSetError,
    ReaderError,
    ReadersNotSetError,
    RecorderNotSetError,
    TargetHumidityNotSetError,
)
from humctrl.pumps import (
    BlendFlow,
    DualPumps,
    Flows,
    MaxFullRangeMax,
    PumpsOutput,
)
from humctrl.readers import (
    Reader,
    Readers,
    ReaderSource,
    Reading,
    Readings,
)
from humctrl.recorder import Recorder
from humctrl.state import Spec, State, View
from humctrl.typing import NonNegative, Normalised, Percent, Positive
from humctrl.utils import PeriodicLoop, Topic, UnsetType, WithWarning, require


class Overdriven(Enum):
    WET = "wet"
    DRY = "dry"

    def __float__(self) -> float:
        match self:
            case Overdriven.WET:
                return 1.0
            case Overdriven.DRY:
                return 0.0


@dataclass(slots=True, frozen=True)
class Msg:
    time: Time


@dataclass(slots=True, frozen=True)
class ErrorMsg(Msg):
    error: str


class SystemErrors(Protocol):
    pumps: UnsetType | Exception | None
    stream: UnsetType | Exception | None
    regulator: UnsetType | Exception | None


class Manager:
    _pumps: DualPumps | None = None
    _control_law: ControlLaw = OPEN_LOOP_LAW
    _recorder: Recorder | None = None
    _readers: Readers | None = None
    _clock: Clock
    lock: RLock
    _process_thread: Thread | None = None

    _recording: bool = False
    _process_time: Positive

    _dry_humidity_fallback: Percent | None = None
    _wet_humidity_fallback: Percent | None = None

    _program: ControlProgram | None = None
    program_running: bool = False
    _runner: Runner | None = None

    _default_stream_flow: BlendFlow = MaxFullRangeMax
    controller: Controller | None = None

    _state_topic: Topic[State]
    _readings_topic: Topic[Readings]

    _warnings_topic: Topic[Any]

    # Cached state
    _pump_outputs: PumpsOutput | None = None
    process_reading: Reading | None = None
    _dry_reading: Reading | Percent | None = None
    _wet_reading: Reading | Percent | None = None
    expected_humidity: Percent | None = None

    def __init__(
        self,
        pumps: DualPumps | None = None,
        process_time: float = 1.0,
        control_law: ControlLaw | ControlLawConfig | None = None,
        recorder: Recorder | None = None,
        process_reader: Reader | None = None,
        dry_humidity: float = 0,
        wet_humidity: float = 100,
    ) -> None:
        self._pumps = pumps
        if control_law is not None:
            self.set_control_law(control_law)

        self._recorder = recorder
        self._process_reader = process_reader
        self._dry_humidity_fallback = dry_humidity
        self._wet_humidity_fallback = wet_humidity

        self._clock = Clock()
        self.lock = RLock()
        self._main_thread = PeriodicLoop(self.main_step, process_time)

        self._readings_topic = Topic[Readings]()
        self._state_topic = Topic[State]()
        self._warnings_topic = Topic[Any]()

    @property
    def spec(self) -> Spec:
        return Spec(
            start=self._clock.start_time,
            process_time=self._main_thread.loop_time,
            pumps=self._pumps.spec if self._pumps is not None else None,
            default_control_law=self._control_law.config if self._control_law is not None else None,
        )

    # region Properties
    @property
    def running(self) -> bool:
        return self._main_thread.running

    @property
    def recording(self) -> bool:
        return self._recording

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
    def dry_reading(self) -> Reading | None:
        if isinstance(self._dry_reading, Reading):
            return self._dry_reading

    @property
    def wet_reading(self) -> Reading | None:
        if isinstance(self._wet_reading, Reading):
            return self._wet_reading

    @property
    def readings(self) -> Readings:
        return Readings(
            dry=self.dry_reading,
            wet=self.wet_reading,
            process=self.process_reading,
        )

    @property
    def dry_humidity(self) -> Percent | None:
        if isinstance(self._dry_reading, Reading):
            return self._dry_reading.humidity
        return self._dry_reading

    @property
    def wet_humidity(self) -> Percent | None:
        if isinstance(self._wet_reading, Reading):
            return self._wet_reading.humidity
        return self._wet_reading

    @property
    def required_dry_humidity(self) -> Percent:
        return require(self.dry_humidity, PumpHumidityNotSetError, "dry")

    @property
    def required_wet_humidity(self) -> Percent:
        return require(self.wet_humidity, PumpHumidityNotSetError, "wet")

    @property
    def pump_outputs(self) -> PumpsOutput | None:
        return self._pump_outputs

    @property
    def required_pumps_output(self) -> PumpsOutput:
        return require(self._pump_outputs, PumpsNotSetError)

    @property
    def flows(self) -> Flows | None:
        return self._pump_outputs.flows if self._pump_outputs is not None else None

    @property
    def set_point(self) -> Percent | None:
        if self.controller is not None:
            return self.controller.set_point

    @property
    def required_target_humidity(self) -> Percent:
        return require(self.set_point, TargetHumidityNotSetError)

    @property
    def state(self) -> State:

        return State(
            duration_ns=self._clock.elapsed_ns(),
            running=self.running,
            pumps=self._pump_outputs,
            readings=self.readings,
            expected_humidity=self.expected_humidity,
            controller=None if self.controller is None else self.controller.state,
            recording=self.recording,
        )

    @property
    def view(self) -> View:
        return View(
            start=self._clock.start_time,
            duration_ns=self._clock.elapsed_ns(),
            running=self.running,
            process_time=self.spec.process_time,
            pumps=None if self._pumps is None else self._pumps.view,
            readings=self.readings,
            default_control_law=self._control_law.config,
            controller=None if self.controller is None else self.controller.view,
            expected_humidity=self.expected_humidity,
            recording=self.recording,
        )

    # region Topics

    @property
    def state_topic(self) -> Topic[State]:
        """Full state, published whenever the loop advances it."""
        return self._state_topic

    @property
    def readings_topic(self) -> Topic[Readings]:
        """Sensor readings, published on every loop pass."""
        return self._readings_topic

    @property
    def warnings_topic(self) -> Topic[Any]:
        """Faults. Lossy by design, so state carries them too."""
        return self._warnings_topic

    # endregion

    def calculate_expected_humidity(self, wet_fraction: Normalised) -> Percent | None:
        dry_humidity, wet_humidity = self.dry_humidity, self.wet_humidity
        if dry_humidity is not None and wet_humidity is not None:
            return expected_humidity_from_fraction(dry_humidity, wet_humidity, wet_fraction)

    def now(self) -> Time:
        return self._clock.now()

    def now_ns(self) -> int:
        return self._clock.now_ns()

    def now_s(self) -> float:
        return self._clock.now_s()

    def elapsed_ns(self, label: str | None = None) -> int:
        return self._clock.elapsed_ns(label)

    def elapsed_s(self, label: str | None = None) -> float:
        return self.elapsed_ns(label) / 1e9

    def elapsed(self, label: str | None = None) -> Duration:
        return self._clock.elapsed(label)

    # endregion

    # region Setter

    def set_pumps(self, pumps: DualPumps) -> None:
        self._pumps = pumps
        self._update_outputs(pumps.output)

    def set_recorder(self, recorder: Recorder) -> None:
        self._recorder = recorder

    def set_readers(self, reader: Readers) -> None:
        self._readers = reader
        self.read_readers()

    def set_control_law(self, control_law: ControlLaw | ControlLawConfig) -> None:
        self._control_law = (
            control_law.build() if isinstance(control_law, ControlLawConfig) else control_law
        )

    # endregion

    def verify_running(self) -> None:
        if not self.running:
            raise ManagerNotRunningError()

    def require_recorder(self) -> Recorder:
        return require(self._recorder, RecorderNotSetError)

    def require_readers(self) -> Readers:
        return require(self._readers, ReadersNotSetError)

    def require_control_law(self) -> ControlLaw:
        if self._control_law is None:
            raise ControlLawNotSetError()
        return self._control_law

    def require_pumps(self) -> DualPumps:
        if self._pumps is None:
            self._flows = None
            raise PumpsNotSetError()
        return self._pumps

    def require_stream_controller(self) -> Controller:
        if self.controller is None:
            raise ControllerNotRunningError()
        return self.controller

    # def update_output(self, output: PumpsOutput):

    def record_state(self) -> None:
        self.require_recorder().record_state(self.state)

    # region Publishers

    def publish_readings(self) -> None:
        self._readings_topic.publish(
            Readings(process=self.process_reading, dry=self.dry_reading, wet=self.wet_reading)
        )

    def publish_state(self) -> None:
        self._state_topic.publish(self.state)

    def publish_warning(self, error: Exception | None) -> None:
        if error is not None:
            self._warnings_topic.publish(ErrorMsg(self.now(), str(error)))

    def on_pump_error(self, error: Exception) -> None:
        self._pump_outputs = None
        self._warnings_topic.publish(ErrorMsg(self.now(), str(error)))

    # endregion

    # region Pumps control

    @contextmanager
    def manual_pumps(self) -> Generator[DualPumps]:
        """Take the loop off the pumps and hand them over, under the lock."""
        with self.lock:
            pumps = self.require_pumps()
            if self.controller is not None:
                self.controller.suspend()
            yield pumps

    def set_flows(self, wet: NonNegative, dry: NonNegative) -> PumpsOutput:
        with self.manual_pumps() as pumps:
            try:
                return self._update_outputs(pumps.set_flows(wet, dry))
            except Exception as e:
                self.on_pump_error(e)
                raise e

    def set_blend(self, flow: BlendFlow, wet_fraction: Normalised) -> PumpsOutput:
        with self.manual_pumps() as pumps:
            try:
                return self._update_outputs(pumps.set_blend(flow, wet_fraction))
            except Exception as e:
                self.on_pump_error(e)
                raise e

    def set_efforts(self, wet: Normalised, dry: Normalised) -> PumpsOutput:
        with self.manual_pumps() as pumps:
            try:
                return self._update_outputs(pumps.set_efforts(wet, dry))
            except Exception as e:
                self.on_pump_error(e)
                raise e

    def stop_pumps(self) -> None:
        with self.manual_pumps() as pumps:
            try:
                pumps.stop()
                self._update_outputs(pumps.output)
            except Exception as e:
                self.on_pump_error(e)
                raise e

    def _update_outputs(
        self,
        outputs: PumpsOutput,
    ) -> PumpsOutput:
        self._pump_outputs = outputs
        wet_humidity, dry_humidity = self.wet_humidity, self.dry_humidity
        if wet_humidity is not None and dry_humidity is not None:
            self.expected_humidity = get_expected_humidity_from_flows(
                dry_humidity, wet_humidity, outputs.flows
            )
        return outputs

    # endregion

    # region Regulation

    def start_controller(
        self,
        humidity: Percent,
        flow: BlendFlow | None = None,
        control_law: ControlLaw | ControlLawConfig | None = None,
    ) -> None:
        with self.lock:
            if control_law is None:
                control_law = self.require_control_law()
            if isinstance(control_law, OpenLoopLaw):
                self.start_open_loop_controller(humidity=humidity, flow=flow)
            else:
                self._start_controller(
                    ClosedLoop(
                        pumps=self.require_pumps(),
                        flow=flow if flow is not None else self._default_stream_flow,
                        humidity=humidity,
                        dry=self.required_dry_humidity,
                        wet=self.required_wet_humidity,
                        control_law=control_law,
                        reading=self.required_process_reading,
                    )
                )

    def start_open_loop_controller(
        self,
        humidity: Percent,
        flow: BlendFlow | None = None,
    ) -> None:
        with self.lock:
            self._start_controller(
                OpenLoop(
                    pumps=self.require_pumps(),
                    flow=flow if flow is not None else self._default_stream_flow,
                    humidity=humidity,
                    dry=self.required_dry_humidity,
                    wet=self.required_wet_humidity,
                )
            )

    def _start_controller(self, controller: Controller) -> None:
        if self.controller is not None:
            raise ControllerAlreadyRunningError(self.controller, controller)
        self.controller = controller

    def resume_controller(self) -> None:
        """Give the suspended controller the pumps back."""
        with self.lock:
            controller = self.require_stream_controller()
            controller.set_resume()
            if isinstance(controller, ClosedLoop):
                controller.resume(self.required_process_reading, self.expected_humidity)
            self._update_stream_state(controller.apply())

    def _update_stream_state(self, stream_state: WithWarning[PumpsOutput]) -> None:
        if stream_state.warning is not None:
            self.publish_warning(stream_state.warning)
        self._update_outputs(stream_state())

    def update_target(self, humidity: Percent) -> None:
        with self.lock:
            self.require_stream_controller().update_set_point(humidity)

    def update_stream_flow(self, flow: BlendFlow, run: bool = False) -> None:
        with self.lock:
            self.require_stream_controller().update_flow(flow)

    # endregion

    def stop_main(self) -> None:
        with self.lock:
            self.stop_recording()
            self._main_thread.stop()

    def stop(self) -> None:
        with self.lock:
            self.stop_main()
            self.stop_pumps()

    def start_recording(self, flag: str | None = None) -> None:
        with self.lock:
            self.verify_running()
            recorder = self.require_recorder()
            self._recording = True
            if flag is not None:
                recorder.add_flag(flag, self.elapsed_ns())

    def stop_recording(self, flag: str | None = None) -> None:
        with self.lock:
            self._recording = False
            if flag is not None:
                self.require_recorder().add_flag(flag, self.elapsed_ns())

    def add_recorder_flag(self, flag: str) -> None:
        with self.lock:
            self.require_recorder().add_flag(flag, self.elapsed_ns())

    def start_main(self) -> None:
        with self.lock:
            if not self.running:
                self._main_thread.start()

    def read_readers(self) -> Readings:
        readings = self.require_readers().read_all()
        with self.lock:
            self.update_readings(readings)
            return readings

    def update_readings(self, readings: Readings) -> None:
        for reader, reading in readings:
            if isinstance(reading, Reading):
                if reader == ReaderSource.PROCESS:
                    self.process_reading = reading
                elif reader == ReaderSource.DRY:
                    self._dry_reading = reading
                elif reader == ReaderSource.WET:
                    self._wet_reading = reading
            elif isinstance(reading, Exception):
                self.publish_warning(ReaderError(reader, reading))
        self.publish_readings()

    def main_step(self) -> None:
        readings = self.read_readers()
        with self.lock:
            try:
                if self._runner is not None and isinstance(readings.process, Reading):
                    self._runner.step(readings.process)
                if self.controller is not None:
                    self.controller.update_readings(readings)
                    self.apply_running_controller(self.controller)

                self.publish_state()
            except Exception as e:
                self.publish_warning(e)

    def apply_running_controller(self, controller: Controller) -> None:
        if not controller.suspended:
            self._update_stream_state(controller.apply())

    def apply(self) -> None:
        with self.lock:
            if self.controller is not None:
                self.apply_running_controller(self.controller)
            self.publish_state()

    def run_command(self, command: Command) -> None:
        if self._runner is not None:
            raise CommandAlreadyRunningError(self._runner, command)
        with self.lock:
            self._runner = command(self)
        if self._runner is not None:
            self.apply()
            self._runner.hold()
            self._runner = None

    def interrupt(self) -> None:
        with self.lock:
            if self._runner is not None:
                self._runner.interrupt()
                self._runner = None

    def run_program(self, program: ControlProgram | list[Command]) -> None:
        with self.lock:
            if self._program is not None and self._program.running:
                raise HumCtrlError(
                    "A program is already running. Interrupt it before starting a new one."
                )
        if isinstance(program, list):
            program = ControlProgram(program)
        self._program = program
        self._program.run(self)
        self._program = None
