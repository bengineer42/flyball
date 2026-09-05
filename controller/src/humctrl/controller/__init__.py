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
    "Controller",
    "ControllerState",
    "ControllerSuspendedError",
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
    flow: BlendFlow
    set_point: Percent
    pumps: DualPumps
    dry: Percent
    wet: Percent
    law: ControlLaw
    correction: UnclampedPercent = 0.0
    _suspended: bool = False

    def __init__(
        self,
        pumps: DualPumps,
        flow: BlendFlow,
        set_point: UnclampedPercent,
        dry: Percent,
        wet: Percent,
        law: ControlLaw | ControlLawConfig | ControlLawView | Tuning,
    ) -> None:
        self.flow = flow
        self.set_point = set_point
        self.pumps = pumps
        self.dry = dry
        self.wet = wet
        self._suspended = False
        self.law = law if isinstance(law, ControlLaw) else law.build()

    @property
    def demand(self) -> UnclampedPercent:
        return self.set_point + self.correction

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

    def set_resume(self) -> None:
        if not self._suspended:
            raise RuntimeError("Controller is not suspended")
        self._suspended = False

    def set_stream(self, flow: BlendFlow | None = None, humidity: Percent | None = None) -> None:
        if humidity is not None:
            self.update_set_point(humidity)
        if flow is not None:
            self.update_flow(flow)

    def update_set_point(self, set_point: Percent) -> None:
        self.set_point = set_point

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
        self.correction = self.law.step(
            reading.time, reading.humidity, self.set_point, self.correction
        )

    def apply(self) -> WithWarning[PumpsOutput]:
        wet_fraction = calculate_wet_fraction(self.dry, self.wet, self.demand)
        outputs = self.pumps.set_blend(self.flow, float(wet_fraction))
        if isinstance(wet_fraction, Rail) and not (self.dry <= self.set_point <= self.wet):
            return WithWarning(outputs, HumidityRailError(self.dry, self.wet, self.set_point))
        return WithWarning(outputs, None)

    def resume(self, reading: Reading, expected: Percent | None = None) -> None:
        """Take the pumps back, holding the humidity they are already delivering.

        Compare ``demand`` against ``expected`` afterwards to size the step: a
        law with no integral cannot hold an offset, so it resumes at whatever
        its proportional term gives and the hand-back bumps the pumps.

        Args:
            reading: The reading to resume from.
            expected: The humidity the pumps are currently delivering. Without
                it the correction this controller last commanded is held.
        """
        if expected is not None:
            self.correction = expected - self.set_point
        self.correction = self.law.resume(
            reading.time, reading.humidity, self.set_point, self.correction
        )
