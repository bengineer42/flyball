from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from threading import Event, RLock, Thread
from typing import Any, overload

from humctrl.clock import Clock, Duration, Time
from humctrl.cmds import Command, ControlProgram
from humctrl.controller import (
    ControlLaw,
    ControlLawConfig,
    ControlLawView,
    ControllerSuspendedError,
    DualPumpController,
    OpenLoopTuning,
    Tuning,
    expected_humidity_from_fraction,
    get_expected_humidity_from_flows,
)
from humctrl.error import (
    CommandAlreadyRunningError,
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
    TuningNotRegisteredError,
)
from humctrl.pumps import (
    Blend,
    BlendFlow,
    DryWet,
    DualPumps,
    Efforts,
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
    SensorNotSetError,
)
from humctrl.recorder import Recorder
from humctrl.resource import Operator, ReleaseReason
from humctrl.resources import ControllerResource
from humctrl.runners import Runner, StartFrom
from humctrl.set_point import SetPointGenerator
from humctrl.state import ActuatorView, ControllerOutput, Spec, State, View
from humctrl.typing import NonNegative, Normalised, Percent, Positive
from humctrl.utils import PeriodicLoop, Topic, WithWarning, require


class Overdriven(Enum):
    WET = "wet"
    DRY = "dry"

    def __float__(self) -> float:
        match self:
            case Overdriven.WET:
                return 1.0
            case Overdriven.DRY:
                return 0.0


class LoggingMsg:
    time: Time


@dataclass(slots=True, frozen=True)
class ErrorMsg(LoggingMsg):
    """A warning or fault, published for anyone watching the rig."""

    time: Time
    detail: str


class Manager:
    _pumps: DualPumps | None = None
    _tunings: dict[str, ControlLawConfig]
    _default_tuning: str
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
    _runner_thread: Thread | None = None

    _default_stream_flow: BlendFlow = MaxFullRangeMax
    controller: DualPumpController | None = None
    _set_point_generator: SetPointGenerator | None = None

    _state_topic: Topic[State]
    _readings_topic: Topic[Readings]

    _warnings_topic: Topic[Any]

    # Cached state
    _pump_outputs: PumpsOutput | None = None
    process_reading: Reading | None = None
    _dry_reading: Reading | Percent | None = None
    _wet_reading: Reading | Percent | None = None
    expected_humidity: Percent | None = None

    _operators: dict[str, Operator] | None = None

    _pumps_owned: bool = False
    _set_point_owned: bool = False
    _flow_owned: bool = False

    def __init__(
        self,
        pumps: DualPumps | None = None,
        process_time: float = 1.0,
        recorder: Recorder | None = None,
        process_reader: Reader | None = None,
        dry_humidity: float = 0,
        wet_humidity: float = 100,
        default_tuning: Tuning | str | None = None,
        tunings: list[Tuning] | None = None,
    ) -> None:
        self._pumps = pumps

        self._recorder = recorder
        self._process_reader = process_reader
        self._dry_humidity_fallback = dry_humidity
        self._wet_humidity_fallback = wet_humidity

        self._clock = Clock()
        self.lock = RLock()
        self._main_thread = PeriodicLoop(self.main_step, process_time)
        self._tunings = {OpenLoopTuning.tag: OpenLoopTuning.config}
        self._default_tuning = OpenLoopTuning.tag
        if tunings is not None:
            # A list of tunings, keyed by tag: ``update`` cannot take a list of
            # objects, and a repeated tag would silently drop one.
            for tuning in tunings:
                if tuning.tag in self._tunings:
                    raise ValueError(f"duplicate tuning tag {tuning.tag!r}")
                self._tunings[tuning.tag] = tuning.config
        if default_tuning is not None:
            self.set_default_tuning(default_tuning)

        self._readings_topic = Topic[Readings]()
        self._state_topic = Topic[State]()
        self._warnings_topic = Topic[Any]()
        self._operator = Operator("manager")

    @property
    def default_tuning(self) -> Tuning:
        return Tuning(tag=self._default_tuning, config=self._tunings[self._default_tuning])

    @property
    def spec(self) -> Spec:
        return Spec(
            start=self._clock.start_time,
            process_time=self._main_thread.loop_time,
            pumps=self._pumps.spec if self._pumps is not None else None,
            default_tuning=self.default_tuning,
        )

    # region Properties
    @property
    def running(self) -> bool:
        return self._main_thread.running

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def tunings(self) -> dict[str, ControlLawConfig | ControlLawView]:
        return self._tunings

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
    def process_sensor_reading(self) -> Reading | None:
        if self._readers is not None and self._readers.process_available:
            return self.process_reading
        raise SensorNotSetError("process")

    @property
    def dry_sensor_reading(self) -> Reading | None:
        if self._readers is not None and self._readers.dry_available:
            return self.dry_reading
        raise SensorNotSetError("dry")

    @property
    def wet_sensor_reading(self) -> Reading | None:
        if self._readers is not None and self._readers.wet_available:
            return self.wet_reading
        raise SensorNotSetError("wet")

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
    def flow_humidities(self) -> DryWet[Percent] | None:
        if self.dry_humidity is not None and self.wet_humidity is not None:
            return DryWet(dry=self.dry_humidity, wet=self.wet_humidity)

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
    def set_humidity(self) -> Percent | None:
        if self.controller is not None:
            return self.controller.set_point

    @property
    def required_set_humidity(self) -> Percent:
        return require(self.set_humidity, TargetHumidityNotSetError)

    @property
    def actuator_view(self) -> ActuatorView:
        return ActuatorView.of(self._pump_outputs, self.expected_humidity)

    @property
    def state(self) -> State:

        return State(
            duration_ns=self._clock.elapsed_ns(),
            running=self.running,
            pumps=self._pump_outputs,
            readings=self.readings,
            expected_humidity=self.expected_humidity,
            controller=None if self.controller is None else self.controller.view,
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
            default_tuning=self.default_tuning,
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
    @overload
    def add_tuning(self, tuning: Tuning, default=False) -> None: ...
    @overload
    def add_tuning(self, tag: str, config: ControlLawConfig, default=False) -> None: ...
    def add_tuning(self, *args, default=False, **kwargs) -> None:
        tuning = kwargs.get("tuning") or next((t for t in args if isinstance(t, Tuning)), None)
        tag = kwargs.get("tag") or next((a for a in args if isinstance(a, str)), None)
        config = kwargs.get("config") or next(
            (a for a in args if isinstance(a, (ControlLawConfig, ControlLawView))), None
        )
        default = next((a for a in args if isinstance(a, bool)), default)
        tag = tag or (tuning.tag if tuning is not None else None)
        config = config or (tuning.config if tuning is not None else None)
        if tag is None:
            raise ValueError("Tag must be specified for the tuning.")
        if config is None:
            raise ValueError("Config must be specified for the tuning.")
        self._tunings[tag] = config
        if default:
            self._default_tuning = tag

    def require_tuning(self, name: str) -> ControlLawConfig | ControlLawView:
        return require(self._tunings.get(name), TuningNotRegisteredError, name)

    def remove_tuning(self, tag: str) -> Tuning | None:
        if (config := self._tunings.pop(tag, None)) is not None:
            return Tuning(tag=tag, config=config)

    def set_default_tuning(self, tuning: str | Tuning) -> Tuning:
        if isinstance(tuning, Tuning):
            self._tunings[tuning.tag] = tuning.config
            tuning, config = tuning.tuple
        else:
            config = self.require_tuning(tuning)
        self._default_tuning = tuning
        return config.to_tuning(tuning)

    def set_pumps(self, pumps: DualPumps) -> None:
        self._pumps = pumps
        self._update_outputs(pumps.output)

    def set_recorder(self, recorder: Recorder) -> None:
        self._recorder = recorder

    def set_readers(self, reader: Readers) -> None:
        self._readers = reader
        self.read_readers()

    # endregion

    def verify_running(self) -> None:
        if not self.running:
            raise ManagerNotRunningError()

    def require_recorder(self) -> Recorder:
        return require(self._recorder, RecorderNotSetError)

    def require_readers(self) -> Readers:
        return require(self._readers, ReadersNotSetError)

    def require_pumps(self) -> DualPumps:
        if self._pumps is None:
            self._flows = None
            raise PumpsNotSetError()
        return self._pumps

    def require_controller(self) -> DualPumpController:
        if self.controller is None:
            raise ControllerNotRunningError()
        return self.controller

    def get_tuning(self, tuning: str | None) -> ControlLawConfig:
        if tuning is None:
            tuning = self._default_tuning
        if tuning not in self._tunings:
            raise TuningNotRegisteredError(tuning)
        return self._tunings[tuning]

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
    def manual_pumps(self) -> Generator[DualPumps, None, None]:
        """Take the pumps for a quick operation under the lock."""
        with self.lock:
            pumps = self.require_pumps()
            if self.controller is not None:
                self._set_point_generator = None
                self.controller.suspend()
            yield pumps

    def set_flows(self, dry: NonNegative, wet: NonNegative) -> PumpsOutput:
        with self.manual_pumps() as pumps:
            try:
                return self._update_outputs(pumps.set_flows(dry, wet))
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

    def set_efforts(self, dry: Normalised, wet: Normalised) -> PumpsOutput:
        with self.manual_pumps() as pumps:
            try:
                return self._update_outputs(pumps.set_efforts(dry, wet))
            except Exception as e:
                self.on_pump_error(e)
                raise e

    def set_pumps_mode(self, pump_mode: Blend | Efforts | Flows) -> PumpsOutput:
        with self.manual_pumps() as pumps:
            try:
                return self._update_outputs(pumps.set_mode(pump_mode))
            except Exception as e:
                self.on_pump_error(e)
                raise e

    def stop_pumps(self) -> PumpsOutput:
        with self.manual_pumps() as pumps:
            try:
                pumps.stop()
                return self._update_outputs(pumps.output)
            except Exception as e:
                self.on_pump_error(e)
                raise e

    def _update_outputs(
        self,
        outputs: PumpsOutput,
    ) -> PumpsOutput:
        self._pump_outputs = outputs
        if humidities := self.flow_humidities:
            self.expected_humidity = get_expected_humidity_from_flows(outputs.flows, humidities)
        return outputs

    # endregion

    # region Regulation

    def start_controller(
        self,
        humidity: Percent,
        flow: BlendFlow | None = None,
        tuning: ControlLaw | ControlLawConfig | ControlLawView | Tuning | str | None = None,
        force: bool = False,
    ) -> ControllerOutput:
        with self.lock:
            controller = self._start_controller(humidity, flow, tuning, force)
            return self.apply_unsuspended_controller(controller)

    def _start_controller(
        self,
        humidity: Percent,
        flow: BlendFlow | None = None,
        tuning: ControlLaw | ControlLawConfig | ControlLawView | Tuning | str | None = None,
        force: bool = False,
    ) -> DualPumpController:
        if isinstance(tuning, str) or tuning is None:
            tuning = self.get_tuning(tuning)
        if self.controller is not None and not force:
            raise ControllerAlreadyRunningError(self.controller, tuning)
        self.controller = DualPumpController(
            self.require_pumps(),
            flow if flow is not None else self._default_stream_flow,
            humidity,
            self.elapsed_s(),
            self.required_dry_humidity,
            self.required_wet_humidity,
            tuning,
        )
        return self.controller

    def suspend_controller(self, pump_mode: Blend | Efforts | Flows | None = None) -> ActuatorView:
        """Take the pumps away from the running controller."""
        with self.lock:
            self._suspend_controller()
            if pump_mode is not None:
                self.set_pumps_mode(pump_mode)
            self.publish_state()
            return self.actuator_view

    def _suspend_controller(self) -> None:
        with self.lock:
            self._set_point_generator = None
            controller = self.require_controller()
            controller.suspend()
            ControllerResource.release(ReleaseReason.RELEASED)

    def resume_controller(self, operator: Operator | None = None) -> ControllerOutput:
        """Give the suspended controller the pumps back."""
        with self.lock:
            controller = self.require_controller()
            ControllerResource.claim(operator or self._operator)
            controller.resume_with_reading(self.required_process_reading, self.expected_humidity)
            return self.apply_unsuspended_controller(controller)

    def _get_set_point_start(self, start_from: StartFrom | Percent = StartFrom.READING) -> Percent:
        if start_from is StartFrom.TARGET:
            return self.required_set_humidity
        elif start_from is StartFrom.READING:
            return self.required_process_humidity
        else:
            return start_from

    def start_set_point_generator(
        self, generator: SetPointGenerator, start_from: StartFrom | Percent = StartFrom.READING
    ) -> tuple[ControllerOutput, float | Event | None]:
        with self.lock:
            controller = self.require_controller()
            self._set_point_generator = generator
            wait = self._set_point_generator.start(
                self.elapsed_s(), self._get_set_point_start(start_from)
            )
            return self.apply_unsuspended_controller(controller), wait

    def remove_set_point_generator(self) -> None:
        with self.lock:
            self._set_point_generator = None

    def _update_stream_state(self, stream_state: WithWarning[PumpsOutput]) -> PumpsOutput:
        if stream_state.warning is not None:
            self.publish_warning(stream_state.warning)
        return self._update_outputs(stream_state())

    def update_set_point(self, humidity: Percent) -> None:
        with self.lock:
            self._set_point_generator = None
            self.require_controller().set_point = humidity

    def update_stream_flow(self, flow: BlendFlow, run: bool = False) -> None:
        with self.lock:
            self.require_controller().update_flow(flow)

    # endregion

    def stop_main(self) -> None:
        with self.lock:
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
                    if self._set_point_generator is not None:
                        self.controller.set_point = self._set_point_generator.generate(
                            self.elapsed_s()
                        )
                    self.controller.update_readings(readings)
                    self.apply_running_controller(self.controller)

                self.publish_state()
            except Exception as e:
                self.publish_warning(e)

    def apply_running_controller(self, controller: DualPumpController) -> PumpsOutput | None:
        if not controller.suspended:
            return self._update_stream_state(controller.apply())

    def apply_unsuspended_controller(self, controller: DualPumpController) -> ControllerOutput:
        if not controller.suspended:
            if self._set_point_generator is not None:
                controller.set_point = self._set_point_generator.generate(self.elapsed_s())
            output = self._update_stream_state(controller.apply())
            self.publish_state()
            return ControllerOutput.from_parts(controller.view, output, self.expected_humidity)

        raise ControllerSuspendedError()

    def apply_state(self) -> None:
        with self.lock:
            if self.controller is not None:
                self.apply_running_controller(self.controller)
            self.publish_state()

    def run_command(
        self, command: Command, background: bool, interrupt: bool = False
    ) -> Any | None:
        """Run ``command`` and apply it, returning the state it produced.

        The runner it leaves, if any, is held afterwards by whichever of
        :meth:`run_command` or :meth:`start_command` was called.

        Returns:
            The state produced by running the command, if any.

        Args:
            command: The command to run.
            background: Whether to run the command in the background.
            interrupt: Stop whatever is running first. Done under the same lock,
                so nothing can slip in between.

        Raises:
            CommandAlreadyRunningError: If another command is still running and
                ``interrupt`` was not asked for.
        """
        with self.lock:
            if interrupt:
                self.interrupt_runner()
            if self._runner is not None:
                raise CommandAlreadyRunningError(self._runner, command)
            response, self._runner = command.run(self)
            if background and self._runner is not None:
                self._runner_thread = Thread(
                    target=self._run_runner, args=(self._runner,), daemon=True
                )
                self._runner_thread.start()
        if not background and self._runner is not None:
            return self._run_runner(self._runner)
        return response

    def _run_runner(self, runner: Runner) -> None:
        """Wait on ``runner``, clearing it however it ends."""
        try:
            if self._runner is not runner:
                return
            runner.dwell()
        except Exception as error:
            self.publish_warning(error)
        finally:
            with self.lock:
                if self._runner is runner:
                    self._runner = None

    def start_runner_thread(self) -> None:
        if self._runner is not None:
            self._runner_thread = Thread(target=self._run_runner, args=(self._runner,), daemon=True)
            self._runner_thread.start()

    def interrupt_runner(self) -> None:
        with self.lock:
            if self._runner is not None:
                self._runner.interrupt()
            if self._runner_thread is not None:
                self._runner_thread.join()
                self._runner_thread = None

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
