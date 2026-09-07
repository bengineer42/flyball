from humctrl.pumps import BlendFlow, DualPumps, PumpsOutput
from humctrl.pumps.types import DryWet
from humctrl.readers import Reading, Readings
from humctrl.typing import Percent, UnclampedPercent
from humctrl.utils import WithWarning

from .errors import (
    ControlLawNotRegisteredError,
    ControllerSuspendedError,
    HumidityRailError,
    PumpHumiditiesError,
)
from .laws import PI, PID, OpenLoop, OpenLoopTuning, P
from .types import (
    ControlLaw,
    ControlLawConfig,
    ControlLaws,
    ControlLawState,
    ControlLawView,
    ControllerState,
    ControllerView,
    Rail,
    Tuning,
)
from .utils import (
    calculate_wet_fraction,
    expected_humidity_from_fraction,
    get_expected_humidity_from_flows,
)

__all__ = [
    "PI",
    "PID",
    "ControlLaw",
    "ControlLawConfig",
    "ControlLawNotRegisteredError",
    "ControlLawState",
    "ControlLawView",
    "ControlLaws",
    "ControllerState",
    "ControllerSuspendedError",
    "DualPumpController",
    "HumidityRailError",
    "OpenLoop",
    "OpenLoopTuning",
    "P",
    "PumpHumiditiesError",
    "Rail",
    "Tuning",
    "calculate_wet_fraction",
    "expected_humidity_from_fraction",
    "get_expected_humidity_from_flows",
]


def get_law_config(tag: str, *args, **kwargs) -> ControlLawConfig:
    if law := ControlLaws.get(tag):
        return law.config(*args, **kwargs)
    raise ControlLawNotRegisteredError(tag)


class Controller:
    set_point: float
    law: ControlLaw
    correction: float = 0.0

    def setup(
        self,
        law: ControlLaw | ControlLawConfig | ControlLawView | Tuning,
        set_point: float,
        time: float,
    ) -> None:
        self.correction = 0.0
        self.set_point = set_point
        self.law = law if isinstance(law, ControlLaw) else law.build()
        self.law.start(time)

    @property
    def demand(self) -> float:
        return self.set_point + self.correction

    @property
    def tag(self) -> str:
        return self.law.tag

    def step(self, time: float, value: float) -> None:
        self.correction = self.law.step(time, value, self.set_point, self.correction)

    def resume(self, time: float, value: float, expected: float | None = None) -> None:
        """Resume the controller from a given state.

        Args:
            time: The time to resume from.
            value: The value to resume from.
            expected: The expected value to resume from.
        """
        if expected is not None:
            self.correction = expected - self.set_point
        self.correction = self.law.resume(time, value, self.set_point, self.correction)


class DualPumpController(Controller):
    flow: BlendFlow
    pumps: DualPumps
    dry: Percent
    wet: Percent
    last_reading: Reading | None = None
    _suspended: bool = False

    def __init__(
        self,
        pumps: DualPumps,
        flow: BlendFlow,
        set_point: UnclampedPercent,
        time: float,
        dry: Percent,
        wet: Percent,
        law: ControlLaw | ControlLawConfig | ControlLawView | Tuning,
    ) -> None:
        self.flow = flow
        self.pumps = pumps
        self.dry = dry
        self.wet = wet
        self.setup(law, set_point, time)
        self._suspended = False

    @property
    def state(self) -> ControllerState:
        return ControllerState(
            set_point=self.set_point,
            correction=self.correction,
            flow=self.flow,
            flow_humidities=DryWet(dry=self.dry, wet=self.wet),
            suspended=self._suspended,
            law=self.law.state,
        )

    @property
    def view(self) -> ControllerView:
        return ControllerView.of(self.law.config, self.state)

    @property
    def suspended(self) -> bool:
        return self._suspended

    @property
    def tag(self) -> str:
        return self.law.tag

    def suspend(self) -> None:
        self._suspended = True

    def set_stream(self, flow: BlendFlow | None = None, humidity: Percent | None = None) -> None:
        if humidity is not None:
            self.set_point = humidity
        if flow is not None:
            self.update_flow(flow)

    def update_flow(self, flow: BlendFlow) -> None:
        self.flow = flow

    def update_readings(self, readings: Readings) -> None:
        if isinstance(readings.dry, Reading):
            self.dry = readings.dry.humidity
        if isinstance(readings.wet, Reading):
            self.wet = readings.wet.humidity
        if not self.suspended and isinstance(readings.process, Reading):
            self.step(readings.process.time, readings.process.humidity)

    def apply(self) -> WithWarning[PumpsOutput]:
        wet_fraction = calculate_wet_fraction(self.dry, self.wet, self.demand)
        outputs = self.pumps.set_blend(self.flow, float(wet_fraction))
        if isinstance(wet_fraction, Rail) and not (self.dry <= self.set_point <= self.wet):
            return WithWarning(outputs, HumidityRailError(self.dry, self.wet, self.set_point))
        return WithWarning(outputs, None)

    def resume_with_reading(self, reading: Reading, expected: Percent | None = None) -> None:
        self.resume(reading.time, reading.humidity, expected)
