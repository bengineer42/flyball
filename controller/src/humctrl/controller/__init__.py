from typing import Any, Protocol

from humctrl.pumps import BlendFlow, DualPumps, Flows, PumpsOutput
from humctrl.pumps.types import DryWet
from humctrl.readers import Reading, Readings
from humctrl.typing import Normalised, Percent, UnclampedPercent
from humctrl.utils import WithWarning

from .errors import HumidityRailError, PumpHumiditiesError
from .laws import OPEN_LOOP_LAW, OpenLoopLaw
from .types import (
    ClosedControllerState,
    ClosedControllerView,
    ControlLaw,
    ControlLawConfig,
    ControlLawView,
    ControllerState,
    ControllerView,
    Rail,
)

__all__ = [
    "OPEN_LOOP_LAW",
    "ClosedLoop",
    "ControlLaw",
    "ControlLawConfig",
    "Controller",
    "ControllerState",
    "HumidityRailError",
    "OpenLoop",
    "OpenLoopLaw",
    "PumpHumiditiesError",
    "Rail",
    "calculate_wet_fraction",
    "expected_humidity_from_fraction",
    "get_expected_humidity_from_flows",
]


def expected_humidity_from_fraction(
    dry_humidity: Percent, wet_humidity: Percent, wet_fraction: Normalised
) -> Percent:
    return dry_humidity + wet_fraction * (wet_humidity - dry_humidity)


def get_expected_humidity_from_flows(
    dry_humidity: Percent, wet_humidity: Percent, flows: Flows
) -> Percent | None:
    total = flows.total
    return (flows.wet * wet_humidity + flows.dry * dry_humidity) / total if total != 0.0 else None


def calculate_wet_fraction(
    dry_humidity: Percent, wet_humidity: Percent, target: Percent
) -> Normalised | Rail:

    if wet_humidity <= dry_humidity:
        raise PumpHumiditiesError(wet=wet_humidity, dry=dry_humidity)
    if target < dry_humidity:
        return Rail.DRY
    if target > wet_humidity:
        return Rail.WET
    return (target - dry_humidity) / (wet_humidity - dry_humidity)


class Controller(Protocol):
    flow: BlendFlow
    demand: UnclampedPercent
    pumps: DualPumps
    dry: Percent
    wet: Percent
    _suspended: bool = False

    @property
    def set_point(self) -> Percent:
        return self.demand

    @property
    def type(self) -> str: ...

    @property
    def spec(self) -> ControlLawConfig | str:
        return self.type

    @property
    def state(self) -> ControllerState:
        return ControllerState(
            set_point=self.set_point,
            flow=self.flow,
            flow_humidities=DryWet(dry=self.dry, wet=self.wet),
            suspended=self._suspended,
        )

    @property
    def view(self) -> ControllerView:
        return ControllerView(
            set_point=self.set_point,
            flow=self.flow,
            flow_humidities=DryWet(dry=self.dry, wet=self.wet),
            suspended=self._suspended,
            law=self.law_view,
        )

    @property
    def law_state(self) -> Any | None:
        return None

    @property
    def law_view(self) -> ControlLawView | str:
        return self.type

    @property
    def suspended(self) -> bool:
        return self._suspended

    def suspend(self) -> None:
        self._suspended = True

    def set_resume(self) -> None:
        if not self._suspended:
            raise RuntimeError("Controller is not suspended")
        self._suspended = False

    def set_stream(self, flow: BlendFlow | None = None, humidity: Percent | None = None) -> None:
        if humidity is not None:
            self.update_set_point(humidity)
        if flow is not None:
            self.update_flow(flow)

    def update_set_point(self, humidity: Percent) -> None:
        self.demand = humidity

    def update_flow(self, flow: BlendFlow) -> None:
        self.flow = flow

    def update_readings(self, readings: Readings) -> None:
        if isinstance(readings.dry, Reading):
            self.dry = readings.dry.humidity
        if isinstance(readings.wet, Reading):
            self.wet = readings.wet.humidity
        if not self.suspended and isinstance(readings.process, Reading):
            self.update_process_reading(readings.process)

    def update_process_reading(self, reading: Reading) -> None:
        return None

    def apply(self) -> WithWarning[PumpsOutput]:
        wet_fraction = calculate_wet_fraction(self.dry, self.wet, self.demand)
        outputs = self.pumps.set_blend(self.flow, float(wet_fraction))
        return WithWarning(outputs, self._update_stream(outputs, wet_fraction))

    def _update_stream(
        self, output: PumpsOutput, wet_fraction: Normalised | Rail
    ) -> Exception | None:
        if isinstance(wet_fraction, Rail):
            return HumidityRailError(self.dry, self.wet, self.set_point)


class OpenLoop(Controller):
    def __init__(
        self,
        pumps: DualPumps,
        flow: BlendFlow,
        humidity: Percent,
        dry: Percent,
        wet: Percent,
    ) -> None:
        self.pumps = pumps
        self.flow = flow
        self.demand = humidity
        self.dry = dry
        self.wet = wet

    @property
    def type(self) -> str:
        return "open_loop"


class ClosedLoop(Controller):
    control_law: ControlLaw
    _set_point: Percent

    last_applied: Percent | None = None

    def __init__(
        self,
        pumps: DualPumps,
        flow: BlendFlow,
        humidity: Percent,
        dry: Percent,
        wet: Percent,
        control_law: ControlLaw | ControlLawConfig,
        reading: Reading,
    ) -> None:
        self.pumps = pumps
        self.flow = flow
        self._set_point = humidity
        self.demand = humidity
        self.dry = dry
        self.wet = wet
        self.control_law = (
            control_law.build() if isinstance(control_law, ControlLawConfig) else control_law
        )
        self.control_law.start(reading.time, reading.humidity)

    @property
    def spec(self) -> ControlLawConfig:
        return self.control_law.config

    @property
    def set_point(self) -> Percent:
        return self._set_point

    @property
    def type(self) -> str:
        return self.control_law.type

    @property
    def law_state(self) -> Any | None:
        return self.control_law.state

    @property
    def law_view(self) -> ControlLawView | str:
        return ControlLawView(
            spec=self.control_law.config,
            state=self.control_law.state,
        )

    @property
    def state(self) -> ClosedControllerState:
        return ClosedControllerState(
            law=self.law_view,
            set_point=self.set_point,
            demand=self.demand,
            flow=self.flow,
            flow_humidities=DryWet(dry=self.dry, wet=self.wet),
            suspended=self._suspended,
        )

    @property
    def view(self) -> ClosedControllerView:
        return ClosedControllerView(
            law=self.law_view,
            set_point=self.set_point,
            demand=self.demand,
            flow=self.flow,
            flow_humidities=DryWet(dry=self.dry, wet=self.wet),
            suspended=self._suspended,
        )

    def resume(self, reading: Reading, expected: Percent | None = None) -> None:
        """Take the pumps back, setting ``demand`` to where control resumes.

        ``expected`` is the humidity the pumps are currently delivering; without
        it the last value this controller applied is used. With neither there is
        no operating point to hold, so the law cold starts and the demand steps
        to the set point. Compare ``demand`` against ``expected`` afterwards to
        size that step: a law with no integral cannot hold an offset either way.
        """
        if expected is not None:
            self.last_applied = expected

        if self.last_applied is None:
            self.control_law.start(reading.time, reading.humidity)
            self.demand = self.set_point
        else:
            self.demand = self.set_point + self.control_law.resume(
                reading.time, reading.humidity, self.set_point, self.last_applied - self.set_point
            )

    def update_process_reading(self, reading: Reading) -> None:
        applied = None if self.last_applied is None else self.last_applied - self.set_point
        self.demand = self.set_point + self.control_law.step(
            reading.time, reading.humidity, self.set_point, last_applied=applied
        )

    def update_set_point(self, humidity: Percent) -> None:
        self.demand += humidity - self.set_point
        self._set_point = humidity

    def _update_stream(
        self, outputs: PumpsOutput, wet_fraction: Normalised | Rail
    ) -> Exception | None:
        self.last_applied = get_expected_humidity_from_flows(self.dry, self.wet, outputs.flows)
        if isinstance(wet_fraction, Rail) and not (self.dry <= self.set_point <= self.wet):
            return HumidityRailError(self.dry, self.wet, self.set_point)
        return None
