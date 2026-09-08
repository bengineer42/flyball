from functools import cached_property
from typing import NoReturn

from humctrl.config import Config, ConfigOr, resolve
from humctrl.typing import NonNegative, Normalised, Positive
from humctrl.utils import format_quantity, validate_normalised

from .drivers import DualPumpDriver
from .errors import FlowsOverdrivenError, PumpError
from .types import (
    Absolute,
    Blend,
    BlendFlow,
    CurrentBlend,
    DryWet,
    Efforts,
    Flows,
    MaxFlows,
    MaxFullRangeMax,
    OfBlendMax,
    OfFullRangeMax,
    OnOverdrive,
    PumpOutput,
    PumpsMode,
    PumpsOutput,
    PumpsSpec,
    PumpsView,
)

__all__ = [
    "Absolute",
    "Blend",
    "BlendFlow",
    "CurrentBlend",
    "DryWet",
    "DualPumpDriver",
    "DualPumps",
    "Efforts",
    "Flows",
    "FlowsOverdrivenError",
    "MaxFullRangeMax",
    "OfBlendMax",
    "OfFullRangeMax",
    "OnOverdrive",
    "PumpError",
    "PumpOutput",
    "PumpsMode",
    "PumpsOutput",
    "PumpsSpec",
    "PumpsView",
]


class DualPumps:
    pumps: DualPumpDriver
    units: str | None = None
    max_flows: MaxFlows

    _target_wet_fraction: Normalised | None = None

    def __init__(
        self,
        pumps: ConfigOr[DualPumpDriver],
        max_flows: MaxFlows,
        units: str | None = None,
    ) -> None:
        self.pumps = resolve(pumps)
        self.max_flows = max_flows
        self.units = units

    @property
    def dry_max_flow(self) -> Positive:
        return self.max_flows.dry

    @property
    def wet_max_flow(self) -> Positive:
        return self.max_flows.wet

    @cached_property
    def max_full_range_flow(self) -> Positive:
        return min(self.max_flows.dry, self.max_flows.wet)

    @property
    def spec(self) -> PumpsSpec:
        return PumpsSpec(
            max_flows=self.max_flows, full_range_max_flow=self.max_full_range_flow, units=self.units
        )

    @property
    def dry_effort(self) -> Normalised:
        return self.pumps.dry_effort

    @property
    def wet_effort(self) -> Normalised:
        return self.pumps.wet_effort

    @property
    def efforts(self) -> Efforts:
        return self.pumps.efforts

    @property
    def dry_flow(self) -> NonNegative:
        return self.dry_max_flow * self.dry_effort

    @property
    def wet_flow(self) -> NonNegative:
        return self.wet_max_flow * self.wet_effort

    @property
    def flows(self) -> Flows:
        return self.efforts.to_flows(self.dry_max_flow, self.wet_max_flow)

    @property
    def dry_output(self) -> PumpOutput:
        return PumpOutput(effort=self.dry_effort, flow=self.dry_flow)

    @property
    def wet_output(self) -> PumpOutput:
        return PumpOutput(effort=self.wet_effort, flow=self.wet_flow)

    @property
    def output(self) -> PumpsOutput:
        return self.efforts_to_outputs(self.efforts)

    @property
    def blend(self) -> CurrentBlend:
        return self.flows.blend

    @property
    def total_flow(self) -> NonNegative:
        return self.flows.total

    @property
    def dry_fraction(self) -> Normalised:
        return self.flows.dry_fraction

    @property
    def wet_fraction(self) -> Normalised:
        return self.flows.wet_fraction

    @property
    def blend_effort(self) -> Normalised:
        efforts = self.efforts
        return max(efforts.dry, efforts.wet)

    @property
    def view(self) -> PumpsView:
        return PumpsView.of(self.spec, self.output)

    def flow_str(self, flow: NonNegative) -> str:
        return format_quantity(flow, units=self.units)

    def max_flow_at(self, wet_fraction: Normalised) -> NonNegative:
        return self._max_flow_at(validate_normalised("wet_fraction", wet_fraction))

    def _max_flow_at(self, wet_fraction: Normalised) -> NonNegative:
        if wet_fraction <= 0.0:
            return self.dry_max_flow
        if wet_fraction >= 1.0:
            return self.wet_max_flow
        return min(self.dry_max_flow / (1.0 - wet_fraction), self.wet_max_flow / wet_fraction)

    def flows_to_efforts(self, dry: NonNegative, wet: NonNegative) -> Efforts:
        return Efforts(dry=dry / self.dry_max_flow, wet=wet / self.wet_max_flow)

    def validate_flows(
        self,
        dry: NonNegative,
        wet: NonNegative,
        wet_fraction: Normalised | None = None,
    ) -> Efforts:
        efforts = self.flows_to_efforts(dry, wet)
        if efforts.overdriven:
            raise FlowsOverdrivenError(
                dry_flow=dry,
                wet_flow=wet,
                max_flows=self.max_flows,
                units=self.units,
                wet_fraction=wet_fraction,
            )
        return efforts

    def raise_flow_overdriven(
        self, dry: NonNegative, wet: NonNegative, wet_fraction: Normalised | None = None
    ) -> NoReturn:
        raise FlowsOverdrivenError(
            dry_flow=dry,
            wet_flow=wet,
            max_flows=self.max_flows,
            units=self.units,
            wet_fraction=wet_fraction,
        )

    def set_blend(
        self,
        flow: BlendFlow,
        wet_fraction: Normalised,
    ) -> PumpsOutput:
        if isinstance(flow, Absolute):
            flows = Flows.from_blend(flow.value, wet_fraction)
            efforts = flows.to_efforts(self.dry_max_flow, self.wet_max_flow)
            if efforts.overdriven:
                if flow.on_overdrive == OnOverdrive.RAISE:
                    self.raise_flow_overdriven(flows.dry, flows.wet, wet_fraction)
                efforts = efforts.derated()[0]

        else:
            if isinstance(flow, OfBlendMax):
                flows = Flows.from_blend(flow.value * self._max_flow_at(wet_fraction), wet_fraction)
            else:
                flows = Flows.from_blend(flow.value * self.max_full_range_flow, wet_fraction)
            efforts = flows.to_efforts(self.dry_max_flow, self.wet_max_flow)

        return self.set_efforts(efforts.dry, efforts.wet)

    def efforts_to_outputs(self, efforts: Efforts) -> PumpsOutput:
        return PumpsOutput(
            efforts=efforts,
            flows=efforts.to_flows(self.dry_max_flow, self.wet_max_flow),
        )

    def set_flows(self, dry: NonNegative, wet: NonNegative) -> PumpsOutput:
        efforts = self.validate_flows(dry, wet)
        return self.set_efforts(efforts.dry, efforts.wet)

    def set_efforts(self, dry: Normalised, wet: Normalised) -> PumpsOutput:
        efforts = self.pumps.set_efforts(dry, wet)
        self._target_wet_fraction = (
            efforts.to_flows(self.dry_max_flow, self.wet_max_flow).wet_fraction
            if efforts.dry + efforts.wet > 0
            else None
        )
        return self.efforts_to_outputs(efforts)

    def set_mode(self, mode: PumpsMode) -> PumpsOutput:
        if isinstance(mode, Blend):
            return self.set_blend(*mode)
        if isinstance(mode, Efforts):
            return self.set_efforts(*mode)
        if isinstance(mode, Flows):
            return self.set_flows(*mode)

    def stop(self) -> None:
        self.pumps.stop()
        self._target_wet_fraction = None


class DualPumpsConfig(Config[DualPumps]):
    units: str | None = None
    max_flows: MaxFlows
    driver: ConfigOr[DualPumpDriver]

    def build(self) -> DualPumps:
        return DualPumps(
            pumps=resolve(self.driver),
            units=self.units,
            max_flows=self.max_flows,
        )
