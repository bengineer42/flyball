from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import TYPE_CHECKING, Any, overload

from humctrl.blender import BlenderState, DualPumpsBlender
from humctrl.clock import Clock, Duration, Time
from humctrl.control import (
    ControlLawConfig,
    ControlLawView,
    Controller,
    OpenLoopTuning,
    SetPointGenerator,
    Tuning,
    ValueSource,
)
from humctrl.control.types import ControlLawLike, Transfer
from humctrl.errors import (
    ProcessReadingNotAvailableError,
    PumpsNotSetError,
    ReaderError,
    ReadersNotSetError,
    RecorderNotSetError,
    RigNotRunningError,
    TargetHumidityNotSetError,
    TuningNotRegisteredError,
)
from humctrl.pumps import (
    Blend,
    BlendFlow,
    SupplyEfforts,
    SupplyFlows,
)
from humctrl.pumps.types import (
    SupplyEffortsLike,
    SupplyFlowsLike,
)
from humctrl.readers import (
    HTReaderSource,
    HTReading,
    HTReadings,
    HTSetReader,
    SensorError,
    SensorNotSetError,
)
from humctrl.recorder import Recorder
from humctrl.resource import Operator
from humctrl.resources import PumpsResource, SetPointProfileResource, SetPointResource
from humctrl.state import Spec, State, View
from humctrl.utils import Labelled, PeriodicLoop, Topic, require

if TYPE_CHECKING:
    from humctrl.typing import Normalised, Percent


class LoggingMsg:
    time: Time


@dataclass(slots=True, frozen=True)
class ErrorMsg(LoggingMsg):
    """A warning or fault, published for anyone watching the rig."""

    time: Time
    detail: str


class ControlMode(Labelled):
    MANUAL = "manual", "Pumps driven directly"
    BLEND = "blend", "A humidity demand, open loop"
    REGULATED = "regulated", "A controller holding a setpoint"
    PROFILED = "profiled", "A controller following a generator"
    PROGRAMMED = "programmed", "A program sequencing steps"

    @property
    def rank(self) -> int:
        return list(ControlMode).index(self)

    @property
    def blended(self) -> bool:
        return self.rank >= ControlMode.BLEND.rank

    @property
    def regulated(self) -> bool:
        return self.rank >= ControlMode.REGULATED.rank


class PumpMode(Enum):
    MANUAL = "manual"
    BLEND = "blend"
    REGULATED = "regulated"

    @property
    def blended(self) -> bool:
        return self in {PumpMode.BLEND, PumpMode.REGULATED}

    @property
    def regulated(self) -> bool:
        return self is PumpMode.REGULATED


class SystemClaims:
    blend: None
    manual: None
    regulated: None
    profiled: None


class Rig:
    controller: Controller
    pumps: DualPumpsBlender | None

    _tunings: dict[str, ControlLawConfig]
    recorder: Recorder | None = None
    readers: HTSetReader | None = None
    clock: Clock

    lock: RLock
    _main_thread: PeriodicLoop

    _recording: bool = False

    _state_topic: Topic[State]
    _readings_topic: Topic[HTReadings]
    _warnings_topic: Topic[Any]
    mode: PumpMode = PumpMode.MANUAL

    _on_tick: dict[Callable[[Rig, HTReadings], None], None]

    # region Cached state
    process_reading: HTReading | None = None
    dry_reading: HTReading | None = None
    wet_reading: HTReading | None = None
    # endregion Cached state

    def __init__(
        self,
        pumps: DualPumpsBlender | None = None,
        process_interval: float = 1.0,
        recorder: Recorder | None = None,
        readers: HTSetReader | None = None,
        tuning: ControlLawLike | str = OpenLoopTuning,
        tunings: list[Tuning] | None = None,
        setpoint: Percent = 50,
    ) -> None:

        self.recorder = recorder
        self.readers = readers
        self.clock = Clock()
        self.lock = RLock()
        self._main_thread = PeriodicLoop(self.tick, process_interval)
        self._tunings = {OpenLoopTuning.tag: OpenLoopTuning.config}
        self._on_tick = {}
        if tunings is not None:
            for tuning in tunings:
                if tuning.tag in self._tunings:
                    raise ValueError(f"duplicate tuning tag {tuning.tag!r}")
                self._tunings[tuning.tag] = tuning.config
        self.pumps = pumps
        self.controller = Controller(self.get_tuning(tuning), setpoint=setpoint, time=self.now_s())
        if self.pumps is not None and setpoint is not None and self.pumps.demand is None:
            self.pumps.update_demand(demand=setpoint)
        self._readings_topic = Topic[HTReadings]()
        self._state_topic = Topic[State]()
        self._warnings_topic = Topic[Any]()
        self._operator = Operator("rig")

    @property
    def spec(self) -> Spec:
        return Spec(
            start=self.clock.start_time,
            process_interval=self._main_thread.loop_time,
            controller=self.controller.spec,
            pumps=self.pumps and self.pumps.spec,
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
    def required_process_reading(self) -> HTReading:
        return require(self.process_reading, ProcessReadingNotAvailableError)

    @property
    def process_humidity(self) -> Percent | None:
        reading = self.process_reading
        if reading is not None:
            return reading.humidity

    @property
    def dry_humidity(self) -> Percent | None:
        return self._dry_reading and self._dry_reading.humidity

    @property
    def wet_humidity(self) -> Percent | None:
        return self._wet_reading and self._wet_reading.humidity

    @property
    def process_sensor_reading(self) -> HTReading | None:
        if self.readers is not None and self.readers.process_available:
            return self.process_reading
        raise SensorNotSetError("process")

    @property
    def process_reading_s(self) -> float | None:
        if self.process_reading is not None:
            return self.process_reading.seconds

    @property
    def dry_sensor_reading(self) -> HTReading | SensorError | None:
        if self.readers is not None:
            return self.dry_reading
        raise SensorNotSetError("dry")

    @property
    def wet_sensor_reading(self) -> HTReading | None:
        if self.readers is not None and self.readers.wet_available:
            return self.wet_reading
        raise SensorNotSetError("wet")

    @property
    def readings(self) -> HTReadings:
        return HTReadings(
            dry=self.dry_reading,
            wet=self.wet_reading,
            process=self.process_reading,
        )

    @property
    def set_humidity(self) -> Percent | None:
        if self.controller is not None:
            return self.controller.require_setpoint

    @property
    def required_set_humidity(self) -> Percent:
        return require(self.set_humidity, TargetHumidityNotSetError)

    @property
    def state(self) -> State:

        return State(
            duration_ns=self.clock.elapsed_ns(),
            running=self.running,
            pumps=self.pumps and self.pumps.state,
            readings=self.readings,
            controller=self.controller.view,
            recording=self.recording,
        )

    @property
    def view(self) -> View:
        return View(
            start=self.clock.start_time,
            duration_ns=self.clock.elapsed_ns(),
            running=self.running,
            process_interval=self.spec.process_interval,
            pumps=self.pumps and self.pumps.view,
            readings=self.readings,
            controller=self.controller.view,
            recording=self.recording,
        )

    # region Topics

    @property
    def state_topic(self) -> Topic[State]:
        """Full state, published whenever the loop advances it."""
        return self._state_topic

    @property
    def readings_topic(self) -> Topic[HTReadings]:
        """Sensor readings, published on every loop pass."""
        return self._readings_topic

    @property
    def warnings_topic(self) -> Topic[Any]:
        """Faults. Lossy by design, so state carries them too."""
        return self._warnings_topic

    # endregion

    def now(self) -> Time:
        return self.clock.now()

    def now_ns(self) -> int:
        return self.clock.now_ns()

    def now_s(self) -> float:
        return self.clock.now_s()

    def elapsed_ns(self, label: str | None = None) -> int:
        return self.clock.elapsed_ns(label)

    def elapsed_s(self, label: str | None = None) -> float:
        return self.elapsed_ns(label) / 1e9

    def elapsed(self, label: str | None = None) -> Duration:
        return self.clock.elapsed(label)

    # endregion

    # region Setter
    @overload
    def add_tuning(self, tuning: Tuning) -> None: ...
    @overload
    def add_tuning(self, tag: str, config: ControlLawConfig) -> None: ...
    def add_tuning(self, *args, **kwargs) -> None:
        tuning = kwargs.get("tuning") or next((t for t in args if isinstance(t, Tuning)), None)
        tag = kwargs.get("tag") or next((a for a in args if isinstance(a, str)), None)
        config = kwargs.get("config") or next(
            (a for a in args if isinstance(a, (ControlLawConfig, ControlLawView))), None
        )
        tag = tag or (tuning.tag if tuning is not None else None)
        config = config or (tuning.config if tuning is not None else None)
        if tag is None:
            raise ValueError("Tag must be specified for the tuning.")
        if config is None:
            raise ValueError("Config must be specified for the tuning.")
        self._tunings[tag] = config

    def remove_tuning(self, tag: str) -> Tuning | None:
        if (config := self._tunings.pop(tag, None)) is not None:
            return Tuning(tag=tag, config=config)

    def set_recorder(self, recorder: Recorder) -> None:
        self.recorder = recorder

    def set_readers(self, reader: HTSetReader) -> None:
        self.readers = reader
        self.read_readers()

    def attach_on_tick(self, callback: Callable[[Rig, HTReadings], None] | None) -> None:
        with self.lock:
            if callback is not None:
                self._on_tick[callback] = None

    def detach_on_tick(self, callback: Callable[[Rig, HTReadings], None] | None) -> None:
        with self.lock:
            if callback is not None:
                self._on_tick.pop(callback)

    # endregion

    def verify_running(self) -> None:
        if not self.running:
            raise RigNotRunningError()

    def require_recorder(self) -> Recorder:
        return require(self.recorder, RecorderNotSetError)

    def require_readers(self) -> HTSetReader:
        return require(self.readers, ReadersNotSetError)

    def require_pumps(self) -> DualPumpsBlender:
        return require(self.pumps, PumpsNotSetError)

    def get_tuning(self, tuning: ControlLawLike | str) -> ControlLawLike | Tuning:
        if isinstance(tuning, str):
            return self.require_tuning(tuning)
        if isinstance(tuning, Tuning) and tuning.tag not in self._tunings:
            self._tunings[tuning.tag] = tuning.config
        return tuning

    def get_tuning_or_none(
        self, tuning: ControlLawLike | str | None
    ) -> ControlLawLike | Tuning | None:
        return None if tuning is None else self.get_tuning(tuning)

    def require_tuning(self, name: str) -> ControlLawConfig | ControlLawView:
        return require(self._tunings.get(name), TuningNotRegisteredError, name)

    def record_state(self) -> None:
        self.require_recorder().record_state(self.state)

    def resolve_value_source(self, value: ValueSource | Percent) -> Percent:
        return self.controller.resolve_value(value)

    def _set_mode(self, mode: ControlMode) -> None:
        pass

    # region Publishers

    def publish_readings(self) -> None:
        self._readings_topic.publish(
            HTReadings(process=self.process_reading, dry=self.dry_reading, wet=self.wet_reading)
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
    # region ManualPumps

    @contextmanager
    def manual_pumps(
        self, *, publish: bool = False, by: Operator | None = None
    ) -> Generator[DualPumpsBlender, None, None]:
        with self.lock:
            pumps = self.require_pumps()
            PumpsResource.claim(by)
            self._set_mode(ControlMode.MANUAL)
            yield pumps
            if publish:
                self.publish_state()

    def set_flows(
        self, flows: SupplyFlowsLike, *, publish: bool = False, by: Operator | None = None
    ) -> BlenderState:
        with self.manual_pumps(publish=publish, by=by) as pumps:
            pumps.set_supply_flows(flows)
            return pumps.state

    def set_fraction_blend(
        self,
        flow: BlendFlow,
        wet_fraction: Normalised,
        *,
        publish: bool = False,
        by: Operator | None = None,
    ) -> BlenderState:
        with self.manual_pumps(publish=publish, by=by) as pumps:
            pumps.set_blend(flow, wet_fraction)
            return pumps.state

    def set_blend(
        self,
        flow: BlendFlow,
        humidity: Percent,
        *,
        publish: bool = False,
        by: Operator | None = None,
    ) -> BlenderState:
        with self.manual_pumps(publish=publish, by=by) as pumps:
            pumps.update_blend(flow=flow, demand=humidity)
            return pumps.state

    def set_efforts(
        self, efforts: SupplyEffortsLike, *, publish: bool = False, by: Operator | None = None
    ) -> BlenderState:
        with self.manual_pumps(publish=publish, by=by) as pumps:
            pumps.set_supply_efforts(efforts)
            return pumps.state

    def set_pumps_mode(
        self,
        mode: Blend | SupplyEfforts | SupplyFlows,
        publish: bool = False,
        by: Operator | None = None,
    ) -> BlenderState:
        with self.manual_pumps(publish=publish, by=by) as pumps:
            pumps.set_pumps(mode)
            return pumps.state

    def stop_pumps(self, publish: bool = False, by: Operator | None = None) -> BlenderState:
        with self.manual_pumps(publish=publish, by=by) as pumps:
            pumps.stop_pumps()
            return pumps.state

    # endregion ManualPumps
    # region Controller
    def resolve_controller_transfer(
        self, transfer: Transfer | None, tuning: ControlLawLike | str | None = None
    ) -> Transfer:

        return transfer or (
            Transfer.NONE if self.mode.regulated and tuning is None else Transfer.TRACK
        )

    def regulate(
        self,
        at: ValueSource | Percent,
        flow: BlendFlow | None = None,
        generator: SetPointGenerator | None = None,
        tuning: ControlLawLike | str | None = None,
        transfer: Transfer | None = None,
        publish: bool = False,
        by: Operator | None = None,
    ) -> None:
        with self.lock:
            pumps = self.require_pumps()
            tuning = self.get_tuning_or_none(tuning)
            (SetPointResource if generator is None else SetPointProfileResource).claim(by)
            transfer = self.resolve_controller_transfer(transfer, tuning)
            now = self.now_s()
            if transfer is not Transfer.NONE or tuning is not None:
                time = self.process_reading_s if (self.process_reading_s is not None) else now
                expected = pumps.expected_humidity
                bump = self.controller.transfer(time, tuning, transfer, expected)
            self.controller.set_reference(at=at, time=now, generator=generator)
            pumps.update_blend(demand=self.controller.demand_at(now), flow=flow)
            if publish:
                self.publish_state()

    def controller_reference(
        self,
        at: ValueSource | Percent,
        generator: SetPointGenerator | None = None,
        publish: bool = False,
        by: Operator | None = None,
    ) -> None:
        with self.lock:
            pumps = self.require_pumps()
            (SetPointResource if generator is None else SetPointProfileResource).claim(by)
            now = self.now_s()
            self.controller.set_reference(at=at, time=now, generator=generator)

            pumps.update_blend(demand=self.controller.demand_at(now))
            if publish:
                self.publish_state()

    # endregion

    # def stop_main(self) -> None:
    #     with self.lock:
    #         self._main_thread.stop()

    # def stop(self) -> None:
    #     with self.lock:
    #         self.stop_main()
    #         self.stop_pumps()

    # def start_recording(self, flag: str | None = None) -> None:
    #     with self.lock:
    #         self.verify_running()
    #         recorder = self.require_recorder()
    #         self._recording = True
    #         if flag is not None:
    #             recorder.add_flag(flag, self.elapsed_ns())

    # def stop_recording(self, flag: str | None = None) -> None:
    #     with self.lock:
    #         self._recording = False
    #         if flag is not None:
    #             self.require_recorder().add_flag(flag, self.elapsed_ns())

    # def add_recorder_flag(self, flag: str) -> None:
    #     with self.lock:
    #         self.require_recorder().add_flag(flag, self.elapsed_ns())

    # def start_main(self) -> None:
    #     with self.lock:
    #         if not self.running:
    #             self._main_thread.start()

    def read_readers(self) -> HTReadings:
        readings = self.require_readers().read_all()
        with self.lock:
            self.update_readings(readings)
            return readings

    def update_readings(self, readings: HTReadings) -> None:
        for reader, reading in readings:
            if isinstance(reading, HTReading):
                if reader == HTReaderSource.PROCESS:
                    self.process_reading = reading
                elif reader == HTReaderSource.DRY:
                    self._dry_reading = reading
                elif reader == HTReaderSource.WET:
                    self._wet_reading = reading
            elif isinstance(reading, Exception):
                self.publish_warning(ReaderError(reader, reading))
        self.publish_readings()

    def run_on_tick(self, readings: HTReadings) -> None:
        with self.lock:
            for callback in self._on_tick:
                try:
                    callback(self, readings)
                except Exception as e:
                    self.publish_warning(e)

    def tick(self) -> None:
        readings = self.read_readers()
        with self.lock:
            if self.pumps is not None:
                self.pumps.update_readings(self.dry_humidity, self.wet_humidity)
            self.run_on_tick(readings)
            if self.mode.blended:
                try:
                    pumps = self.require_pumps()
                    if self.mode is PumpMode.REGULATED and isinstance(readings.process, HTReading):
                        self.controller.step(readings.process.seconds, readings.process.humidity)
                        pumps.update_demand(self.controller.demand_at(self.now_s()))
                    pumps.update_blend()
                except Exception as e:
                    self.publish_warning(e)
            self.publish_state()
