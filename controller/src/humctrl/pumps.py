from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from typing import Protocol

from humctrl.typing import NonNegative, NonZero, Normalised, Positive
from humctrl.utils import format_quantity, validate_normalised


class PumpError(Exception):
    """Base for everything humctrl.pumps raises."""


class PumpErrorGroup(ExceptionGroup, PumpError): ...


class FlowError(PumpError, ValueError):
    """A requested flow or fraction cannot be applied."""


class FlowsOverdrivenError(FlowError):
    wet_flow: NonNegative
    dry_flow: NonNegative
    max_flow: Positive
    max_wet_flow: Positive
    max_dry_flow: Positive
    wet_fraction: Normalised | None
    units: str | None

    def flow_exceeds_max(self, name: str, flow: float, max_flow: float) -> str:
        flow_str = format_quantity(flow, units=self.units)
        max_flow_str = format_quantity(max_flow, units=self.units)
        return f"\n\r{name} flow {flow_str} exceeds {max_flow_str}"

    def __init__(
        self,
        wet_flow: NonNegative,
        dry_flow: NonNegative,
        max_wet_flow: Positive,
        max_dry_flow: Positive,
        max_flow: Positive | None = None,
        wet_fraction: Normalised | None = None,
        units: str | None = None,
    ):
        self.wet_flow = wet_flow
        self.dry_flow = dry_flow
        self.max_flow = max_flow or (max_wet_flow + max_dry_flow)
        self.max_wet_flow = max_wet_flow
        self.max_dry_flow = max_dry_flow
        self.wet_fraction = wet_fraction
        self.units = units
        string = "Flow unreachable."
        if max_flow is not None and self.total_overdriven:
            string += self.flow_exceeds_max("Total", self.total_flow, max_flow)
            if wet_fraction is not None:
                string += f" at wet fraction {wet_fraction:.3f}"
            string += "."
        if self.wet_overdriven:
            string += self.flow_exceeds_max("Wet", wet_flow, max_wet_flow) + "."
        if self.dry_overdriven:
            string += self.flow_exceeds_max("Dry", dry_flow, max_dry_flow) + "."

        super().__init__(string)

    @cached_property
    def total_flow(self) -> NonNegative:
        return self.wet_flow + self.dry_flow

    @cached_property
    def wet_overdriven(self) -> bool:
        return self.wet_flow > self.max_wet_flow

    @cached_property
    def dry_overdriven(self) -> bool:
        return self.dry_flow > self.max_dry_flow

    @cached_property
    def total_overdriven(self) -> bool:
        return self.total_flow > self.max_flow


class PumpHardwareError(PumpError):
    """The underlying device failed."""


class OnOverdrive(Enum):
    """How to handle a requested flow change that exceeds the maximum."""

    RAISE = "raise"  # raise FlowsOverdrivenError
    CLAMP = "clamp"  # clamp to the maximum flow


@dataclass(frozen=True, slots=True)
class Absolute:
    value: NonNegative
    on_overdrive: OnOverdrive = OnOverdrive.RAISE


@dataclass(frozen=True, slots=True)
class OfBlendMax:
    value: NonNegative = 1.0


@dataclass(frozen=True, slots=True)
class OfFullRangeMax:
    value: NonNegative = 1.0


class OnRail(Enum):
    RAISE = "raise"  # raise FlowsOverdrivenError
    CLAMP = "clamp"  # clamp to the maximum flow


type BlendFlow = Absolute | OfBlendMax | OfFullRangeMax

MaxFullRangeMax = OfFullRangeMax()


@dataclass(slots=True, frozen=True)
class Blend:
    wet_fraction: Normalised
    flow: NonNegative
    units: str | None = None


# @dataclass(frozen=True, slots=True)
# class BlendFlow:
#     value: Positive
#     scale: BlendFlowScale = BlendFlowScale.ABSOLUTE

#     @classmethod
#     def full_range_max(cls, value: NormalisedPositive = 1.0) -> BlendFlow:
#         return cls(value, BlendFlowScale.FULL_RANGE_MAX)

#     @classmethod
#     def blend_max(cls, value: NormalisedPositive = 1.0) -> BlendFlow:
#         return cls(value, BlendFlowScale.BLEND_MAX)

#     @classmethod
#     def absolute(cls, value: Positive) -> BlendFlow:
#         return cls(value, BlendFlowScale.ABSOLUTE)

#     def __float__(self) -> float:
#         return self.value


@dataclass(frozen=True, slots=True)
class AbsoluteFlows:
    wet: NonNegative
    dry: NonNegative
    units: str | None = None

    @property
    def total(self) -> NonNegative:
        return self.wet + self.dry

    @property
    def wet_fraction(self) -> Normalised:
        return self.wet / self.total if self.wet > 0 else 0.0

    @property
    def dry_fraction(self) -> Normalised:
        return self.dry / self.total if self.dry > 0 else 0.0

    @classmethod
    def from_blend(
        cls, total: NonNegative, wet_fraction: Normalised, units: str | None = None
    ) -> AbsoluteFlows:
        return cls(total * wet_fraction, total * (1.0 - wet_fraction), units=units)

    @classmethod
    def from_wet_total(
        cls, wet: NonNegative, total: NonNegative, units: str | None = None
    ) -> AbsoluteFlows:
        return cls(wet, total - wet, units=units)

    @classmethod
    def from_dry_total(
        cls, dry: NonNegative, total: NonNegative, units: str | None = None
    ) -> AbsoluteFlows:
        return cls(total - dry, dry, units=units)

    @property
    def blend(self) -> Blend:
        return Blend(self.wet_fraction, self.total, units=self.units)

    def is_valid(self, wet_max: Positive, dry_max: Positive) -> bool:
        return self.wet <= wet_max and self.dry <= dry_max

    def to_efforts(self, wet_max: Positive, dry_max: Positive) -> Efforts:
        return Efforts(wet=self.wet / wet_max, dry=self.dry / dry_max)

    def derated(self, wet_max: Positive, dry_max: Positive) -> AbsoluteFlows:
        if wet_max <= 0 or dry_max <= 0:
            raise ValueError("Max flows must be > 0")
        efforts = self.to_efforts(wet_max, dry_max)
        return self / max(efforts.wet, efforts.dry, 1.0)

    def __truediv__(self, scale: float) -> AbsoluteFlows:
        return AbsoluteFlows(self.wet / scale, self.dry / scale, units=self.units)

    def __mul__(self, scale: float) -> AbsoluteFlows:
        return AbsoluteFlows(self.wet * scale, self.dry * scale, units=self.units)


@dataclass(slots=True, frozen=True)
class Efforts:
    wet: Normalised
    dry: Normalised

    @property
    def overdriven(self) -> bool:
        return self.wet > 1.0 or self.dry > 1.0

    def derated(self) -> tuple[Efforts, float | None]:
        scale = max(self.wet, self.dry, 1.0)
        if scale <= 1.0:
            return self, None
        return Efforts(self.wet / scale, self.dry / scale), 1 / scale

    def to_flows(
        self, wet_max: Positive, dry_max: Positive, units: str | None = None
    ) -> AbsoluteFlows:
        return AbsoluteFlows(self.wet * wet_max, self.dry * dry_max, units=units)

    def __truediv__(self, scale: NonZero) -> Efforts:
        return Efforts(self.wet / scale, self.dry / scale)

    def __mul__(self, scale: float) -> Efforts:
        return Efforts(self.wet * scale, self.dry * scale)


@dataclass(slots=True, frozen=True)
class PumpsOutput:
    flows: AbsoluteFlows
    efforts: Efforts


class PumpDriver(Protocol):
    @property
    def effort(self) -> Normalised: ...

    def set_effort(self, effort: Normalised) -> Normalised: ...

    def stop(self): ...


class DualPumpDriver(Protocol):
    @property
    def wet_effort(self) -> Normalised: ...

    @property
    def dry_effort(self) -> Normalised: ...

    @property
    def efforts(self) -> Efforts:
        return Efforts(self.wet_effort, self.dry_effort)

    def set_efforts(self, wet: Normalised, dry: Normalised) -> Efforts: ...

    def stop(self): ...


class PumpPair(DualPumpDriver):
    def __init__(self, wet: PumpDriver, dry: PumpDriver):
        self.wet = wet
        self.dry = dry

    @property
    def wet_effort(self) -> Normalised:
        return self.wet.effort

    @property
    def dry_effort(self) -> Normalised:
        return self.dry.effort

    def set_efforts(self, wet: Normalised, dry: Normalised) -> Efforts:
        wet_effort = self.set_wet_effort(wet)
        dry_effort = self.set_dry_effort(dry)
        return Efforts(wet_effort, dry_effort)

    def set_wet_effort(self, effort: Normalised) -> Normalised:
        try:
            return self.wet.set_effort(effort)
        except Exception as e:
            raise PumpHardwareError(f"Wet pump error: {e.__class__.__name__}: {e}") from e

    def set_dry_effort(self, effort: Normalised) -> Normalised:
        try:
            return self.dry.set_effort(effort)
        except Exception as e:
            raise PumpHardwareError(f"Dry pump error: {e.__class__.__name__}: {e}") from e

    def stop_wet(self):
        try:
            self.wet.stop()
        except Exception as e:
            raise PumpHardwareError(f"Wet pump failed to stop: {e.__class__.__name__}: {e}") from e

    def stop_dry(self):
        try:
            self.dry.stop()
        except Exception as e:
            raise PumpHardwareError(f"Dry pump failed to stop: {e.__class__.__name__}: {e}") from e

    def stop(self):
        errors: list[PumpHardwareError] = []
        for stop_one in (self.stop_wet, self.stop_dry):
            try:
                stop_one()
            except PumpHardwareError as e:
                errors.append(e)
        if errors:
            raise PumpErrorGroup("Failed to stop pumps", errors)


@dataclass(slots=True, frozen=True, init=False)
class MaxFlows:
    wet: Positive
    dry: Positive
    total: Positive
    full_range: Positive
    units: str | None

    def __init__(
        self,
        wet: Positive,
        dry: Positive,
        total: Positive | None = None,
        full_range: Positive | None = None,
        units: str | None = None,
    ):
        object.__setattr__(self, "wet", wet)
        object.__setattr__(self, "dry", dry)
        object.__setattr__(self, "total", total or (wet + dry))
        object.__setattr__(self, "full_range", full_range or min(wet, dry, self.total))
        object.__setattr__(self, "units", units)


class DualPumpsConfig(Protocol):
    def build(self) -> DualPumps: ...


class DualPumps:
    pumps: DualPumpDriver
    units: str | None = None
    _wet_max_flow: float
    _dry_max_flow: float

    _target_wet_fraction: Normalised | None = None

    def __init__(
        self,
        pumps: DualPumpDriver,
        wet_max_flow: float,
        dry_max_flow: float,
        flow_units: str | None = None,
    ):
        self.pumps = pumps
        self._wet_max_flow = wet_max_flow
        self._dry_max_flow = dry_max_flow
        self.units = flow_units

    @property
    def efforts(self) -> Efforts:
        return self.pumps.efforts

    @property
    def output(self) -> PumpsOutput:
        return self.efforts_to_outputs(self.efforts)

    @property
    def wet_effort(self) -> Normalised:
        return self.pumps.wet_effort

    @property
    def dry_effort(self) -> Normalised:
        return self.pumps.dry_effort

    @property
    def wet_flow(self) -> NonNegative:
        return self.wet_max_flow * self.wet_effort

    @property
    def dry_flow(self) -> NonNegative:
        return self.dry_max_flow * self.dry_effort

    @property
    def flows(self) -> AbsoluteFlows:
        return self.efforts.to_flows(self.wet_max_flow, self.dry_max_flow, units=self.units)

    @property
    def blend(self) -> Blend:
        return self.flows.blend

    @property
    def flow_units(self) -> str | None:
        return self.units

    @property
    def total_flow(self) -> NonNegative:
        return self.flows.total

    @property
    def wet_max_flow(self) -> Positive:
        return self._wet_max_flow

    @property
    def dry_max_flow(self) -> Positive:
        return self._dry_max_flow

    @cached_property
    def max_full_range_flow(self) -> Positive:
        return min(self.wet_max_flow, self.dry_max_flow)

    @property
    def wet_fraction(self) -> Normalised:
        return self.flows.wet_fraction

    @property
    def dry_fraction(self) -> Normalised:
        return self.flows.dry_fraction

    @property
    def blend_effort(self) -> Normalised:
        efforts = self.efforts
        return max(efforts.wet, efforts.dry)

    def flow_str(self, flow: NonNegative) -> str:
        return format_quantity(flow, units=self.units)

    def max_flow_at(self, wet_fraction: Normalised) -> NonNegative:
        return self._max_flow_at(validate_normalised("wet_fraction", wet_fraction))

    def _max_flow_at(self, wet_fraction: Normalised) -> NonNegative:
        if wet_fraction <= 0.0:
            return self.dry_max_flow
        if wet_fraction >= 1.0:
            return self.wet_max_flow
        return min(self.wet_max_flow / wet_fraction, self.dry_max_flow / (1.0 - wet_fraction))

    def flows_to_efforts(self, wet: NonNegative, dry: NonNegative) -> Efforts:
        return Efforts(wet=wet / self.wet_max_flow, dry=dry / self.dry_max_flow)

    def validate_flows(
        self,
        wet: NonNegative,
        dry: NonNegative,
        wet_fraction: Normalised | None = None,
    ) -> Efforts:
        efforts = self.flows_to_efforts(wet, dry)
        if efforts.overdriven:
            raise FlowsOverdrivenError(
                wet_flow=wet,
                dry_flow=dry,
                max_wet_flow=self.wet_max_flow,
                max_dry_flow=self.dry_max_flow,
                units=self.units,
                wet_fraction=wet_fraction,
            )
        return efforts

    def raise_flow_overdriven(
        self, wet: NonNegative, dry: NonNegative, wet_fraction: Normalised | None = None
    ):
        raise FlowsOverdrivenError(
            wet_flow=wet,
            dry_flow=dry,
            max_wet_flow=self.wet_max_flow,
            max_dry_flow=self.dry_max_flow,
            units=self.units,
            wet_fraction=wet_fraction,
        )

    def set_blend(
        self,
        flow: BlendFlow,
        wet_fraction: Normalised,
    ) -> PumpsOutput:
        if isinstance(flow, Absolute):
            flows = AbsoluteFlows.from_blend(flow.value, wet_fraction, units=self.units)
            efforts = flows.to_efforts(self.wet_max_flow, self.dry_max_flow)
            if not efforts.overdriven:
                if flow.on_overdrive == OnOverdrive.RAISE:
                    self.raise_flow_overdriven(flows.wet, flows.dry, wet_fraction)
                efforts = efforts.derated()[0]

        else:
            if isinstance(flow, OfBlendMax):
                flows = AbsoluteFlows.from_blend(
                    flow.value * self._max_flow_at(wet_fraction), wet_fraction, units=self.units
                )
            elif isinstance(flow, OfFullRangeMax):
                flows = AbsoluteFlows.from_blend(
                    flow.value * self.max_full_range_flow, wet_fraction, units=self.units
                )
            efforts = flows.to_efforts(self.wet_max_flow, self.dry_max_flow)

        return self.set_efforts(efforts.wet, efforts.dry)

    def efforts_to_outputs(self, efforts: Efforts) -> PumpsOutput:
        return PumpsOutput(
            efforts=efforts,
            flows=efforts.to_flows(self.wet_max_flow, self.dry_max_flow, units=self.units),
        )

    # def set_blend_flow(

    #     self, flow: BlendFlow | NonNegative, on_overdrive: OnOverdrive = OnOverdrive.RAISE
    # ) -> AbsoluteFlows:
    #     if self._target_wet_fraction is None:
    #         raise PumpError("Cannot set blend flow when wet fraction is unknown")
    #     return self.set_blend(flow, self._target_wet_fraction, on_overdrive=on_overdrive)

    # def set_wet_fraction(
    #     self,
    #     wet_fraction: Normalised,
    #     policy: FractionChangePolicy = FractionChangePolicy.HOLD_OR_RAISE,
    # ) -> AbsoluteFlows:
    #     flow: BlendFlow | NonNegative = self.total_flow
    #     if policy == FractionChangePolicy.TRACKED_MAX:
    #         flow = BlendFlow.blend_max(self.blend_effort)
    #     return self.set_blend(flow, wet_fraction, on_overdrive=policy.on_overdrive)

    # def set_dry_fraction(
    #     self,
    #     dry_fraction: Normalised,
    #     policy: FractionChangePolicy = FractionChangePolicy.HOLD_OR_RAISE,
    # ) -> AbsoluteFlows:
    #     return self.set_wet_fraction(1.0 - dry_fraction, policy=policy)

    def set_flows(self, wet: NonNegative, dry: NonNegative) -> PumpsOutput:
        efforts = self.validate_flows(wet, dry)
        return self.set_efforts(efforts.wet, efforts.dry)

    def set_efforts(self, wet: Normalised, dry: Normalised) -> PumpsOutput:
        efforts = self.pumps.set_efforts(wet, dry)
        self._target_wet_fraction = (
            efforts.to_flows(self.wet_max_flow, self.dry_max_flow, units=self.units).wet_fraction
            if efforts.wet + efforts.dry > 0
            else None
        )
        return self.efforts_to_outputs(efforts)

    def stop(self):
        self.pumps.stop()
        self._target_wet_fraction = None
