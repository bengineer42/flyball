from dataclasses import dataclass
from enum import Enum
from threading import RLock

from humctrl.error import NotReadyError, UnachievableError
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
from humctrl.pumps.types import MaxFlows
from humctrl.typing import NonNegative, Normalised, Percent, Positive
from humctrl.utils import require


class BlenderError(Exception): ...


class HumidityRailError(BlenderError, UnachievableError):
    """The target humidity is outside the range the two lines can mix to.

    Reported on :class:`StreamState` rather than raised: the blend rails to the
    nearest achievable end and the run continues. No amount of pump capacity
    fixes it, so the remedy is a wetter or drier supply, not more flow.
    """

    def __init__(
        self, dry_humidity: Percent, wet_humidity: Percent, target_humidity: Percent
    ) -> None:
        super().__init__(
            f"Target humidity ({target_humidity}%) is outside the achievable range "
            f"{dry_humidity}% (dry) to {wet_humidity}% (wet)."
        )


class PumpHumiditiesError(BlenderError, UnachievableError):
    """The wet and dry line humidities are not in the expected order.

    Raised rather than reported: with no span between the lines there is no
    blend to compute, so the mixing model cannot produce an answer at all.
    """

    def __init__(self, wet: Percent, dry: Percent) -> None:
        super().__init__(
            f"Wet ({wet}) and dry ({dry}) humidities are not in the expected order. Wet humidity "
            "must be greater than dry humidity."
        )


class DemandNotSetError(NotReadyError):
    def __init__(self) -> None:
        super().__init__("Demand not set. Use set_demand() to set the demand before using it.")


class Rail(Enum):
    WET = "wet"
    DRY = "dry"

    def __float__(self) -> float:
        match self:
            case Rail.WET:
                return 1.0
            case Rail.DRY:
                return 0.0


def expected_humidity_from_fraction(
    dry_humidity: Percent, wet_humidity: Percent, wet_fraction: Normalised
) -> Percent:
    return dry_humidity + wet_fraction * (wet_humidity - dry_humidity)


def expected_humidity_from_flows(flows: Flows, humidities: DryWet[Percent]) -> Percent | None:
    total = flows.total
    return (flows * humidities).sum() / total if total != 0.0 else None


def calculate_wet_fraction(humidities: DryWet[Percent], target: Percent) -> Normalised | Rail:

    if humidities.wet <= humidities.dry:
        raise PumpHumiditiesError(wet=humidities.wet, dry=humidities.dry)
    if target < humidities.dry:
        return Rail.DRY
    if target > humidities.wet:
        return Rail.WET
    return (target - humidities.dry) / (humidities.wet - humidities.dry)


@dataclass(frozen=True, slots=True)
class BlenderState(PumpsOutput):
    humidities: DryWet[Percent]
    demand: Percent | None
    expected_humidity: Percent | None


@dataclass(frozen=True, slots=True)
class BlenderSpec:
    max_flows: MaxFlows
    full_range_max_flow: Positive
    flow_units: str | None
    blend_flow: BlendFlow


@dataclass(frozen=True, slots=True)
class BlenderView(BlenderState):
    max_flows: MaxFlows
    full_range_max_flow: Positive
    flow_units: str | None
    blend_flow: BlendFlow

    @classmethod
    def of(cls, spec: BlenderSpec, state: BlenderState) -> "BlenderView":
        return cls(
            humidities=state.humidities,
            demand=state.demand,
            flows=state.flows,
            efforts=state.efforts,
            expected_humidity=state.expected_humidity,
            blend_flow=spec.blend_flow,
            full_range_max_flow=spec.full_range_max_flow,
            flow_units=spec.flow_units,
            max_flows=spec.max_flows,
        )


class DualPumpsBlender:
    pumps: DualPumps
    humidities: DryWet[Percent]
    lock: RLock
    _demand: Percent | None
    output: PumpsOutput
    blend_flow: BlendFlow
    expected_humidity: Percent | None
    _updated: bool = False

    def __init__(
        self,
        pumps: DualPumps,
        humidities: DryWet[Percent],
        demand: Percent | None = None,
        flow: BlendFlow = MaxFullRangeMax,
    ):
        self.pumps = pumps
        self.blend_flow = flow
        self._demand = demand
        self.humidities = humidities
        self.expected_humidity = None
        self.lock = RLock()

    @property
    def flow_humidities(self) -> DryWet[Percent]:
        return self.humidities

    @property
    def demand(self) -> Percent | None:
        return self._demand

    @property
    def required_demand(self) -> Percent:
        return require(self._demand, DemandNotSetError)

    @property
    def spec(self) -> BlenderSpec:
        pumps = self.pumps.spec
        return BlenderSpec(
            max_flows=pumps.max_flows,
            full_range_max_flow=pumps.full_range_max_flow,
            flow_units=pumps.units,
            blend_flow=self.blend_flow,
        )

    @property
    def state(self) -> BlenderState:
        return BlenderState(
            humidities=self.flow_humidities,
            demand=self._demand,
            flows=self.output.flows,
            efforts=self.output.efforts,
            expected_humidity=self.expected_humidity,
        )

    @property
    def view(self) -> BlenderView:
        return BlenderView.of(self.spec, self.state)

    def _update_demand(self, demand: Percent) -> None:
        if self._demand != demand:
            self._updated = True
        self._demand = demand

    def _update_flow(self, flow: BlendFlow) -> None:
        if self.blend_flow != flow:
            self._updated = True
        self.blend_flow = flow

    def _update_readings(self, dry: Percent | None = None, wet: Percent | None = None) -> None:
        if dry is not None and self.humidities.dry != dry:
            self._updated = True
            self.humidities.dry = dry
        if wet is not None and wet != self.humidities.wet:
            self._updated = True
            self.humidities.wet = wet

    def set_flows(self, dry: NonNegative, wet: NonNegative):
        with self.lock:
            self._update_outputs(self.pumps.set_flows(dry, wet))

    def set_blend(self, flow: BlendFlow, wet_fraction: Normalised):
        with self.lock:
            self._update_outputs(self.pumps.set_blend(flow, wet_fraction))

    def set_efforts(self, dry: Normalised, wet: Normalised):
        with self.lock:
            self._update_outputs(self.pumps.set_efforts(dry, wet))

    def set_pumps_mode(self, pump_mode: Blend | Efforts | Flows):
        with self.lock:
            self._update_outputs(self.pumps.set_mode(pump_mode))

    def stop_pumps(self):
        with self.lock:
            self.pumps.stop()
            self._update_outputs(self.pumps.output)

    def _update(
        self,
        demand: Percent | None,
        dry: Percent | None = None,
        wet: Percent | None = None,
        flow: BlendFlow | None = None,
    ) -> bool:
        self._update_readings(dry, wet)
        if demand is not None:
            self._update_demand(demand)
        if flow is not None:
            self._update_flow(flow)
        return self._updated

    def update_flow(self, flow: BlendFlow) -> None:
        with self.lock:
            self._update_flow(flow)

    def update_demand(self, demand: Percent) -> None:
        with self.lock:
            self._update_demand(demand)

    def update_readings(self, dry: Percent | None = None, wet: Percent | None = None) -> None:
        with self.lock:
            self._update_readings(dry, wet)

    def update(
        self,
        demand: Percent | None,
        dry: Percent | None = None,
        wet: Percent | None = None,
        flow: BlendFlow | None = None,
    ) -> bool:
        with self.lock:
            return self._update(demand, dry, wet, flow)

    def _update_outputs(self, output: PumpsOutput) -> PumpsOutput:
        self.pump_error = None
        self.output = output
        self.expected_humidity = expected_humidity_from_flows(
            self.output.flows, self.flow_humidities
        )
        return output

    def update_blend(
        self,
        demand: Percent | None = None,
        dry: Percent | None = None,
        wet: Percent | None = None,
        flow: BlendFlow | None = None,
    ) -> None:
        with self.lock:
            self._update(demand, dry, wet, flow)
            if self._updated and self.demand is not None:
                fraction = calculate_wet_fraction(self.humidities, self.demand)
                self._update_outputs(self.pumps.set_blend(self.blend_flow, float(fraction)))
                self._updated = False
