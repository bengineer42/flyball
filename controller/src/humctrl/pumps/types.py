from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Self, cast

from humctrl.typing import NonNegative, Normalised, Percent, Positive


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
    value: Normalised = 1.0


@dataclass(frozen=True, slots=True)
class OfFullRangeMax:
    value: Normalised = 1.0


type BlendFlow = Absolute | OfBlendMax | OfFullRangeMax

MaxFullRangeMax = OfFullRangeMax()


@dataclass(slots=True, frozen=True)
class DryWet[T: float]:
    dry: T
    wet: T

    @classmethod
    def __cast_floats(
        cls,
        dry: float,
        wet: float,
    ) -> Self:
        return cls(cast("T", dry), cast("T", wet))

    def __iter__(self) -> Iterator[T]:
        yield self.dry
        yield self.wet

    def __truediv__(self, scale: float) -> Self:
        return type(self).__cast_floats(self.dry / scale, self.wet / scale)

    def __mul__(self, scale: float) -> Self:
        return type(self).__cast_floats(self.dry * scale, self.wet * scale)


@dataclass(frozen=True, slots=True)
class Flows(DryWet[NonNegative]):
    @classmethod
    def from_blend(cls, total: NonNegative, wet_fraction: Normalised) -> Flows:
        return cls(total * (1.0 - wet_fraction), total * wet_fraction)

    @classmethod
    def from_wet_total(cls, wet: NonNegative, total: NonNegative) -> Flows:
        return cls(total - wet, wet)

    @classmethod
    def from_dry_total(cls, dry: NonNegative, total: NonNegative) -> Flows:
        return cls(dry, total - dry)

    @property
    def blend(self) -> Blend:
        return Blend(self.wet_fraction, self.total)

    @property
    def total(self) -> float:
        return self.dry + self.wet

    @property
    def dry_fraction(self) -> float:
        return self.dry / self.total if self.dry > 0 else 0.0

    @property
    def wet_fraction(self) -> float:
        return self.wet / self.total if self.wet > 0 else 0.0

    def is_valid(self, dry_max: Positive, wet_max: Positive) -> bool:
        return self.dry <= dry_max and self.wet <= wet_max

    def to_efforts(self, dry_max: Positive, wet_max: Positive) -> Efforts:
        return Efforts(dry=self.dry / dry_max, wet=self.wet / wet_max)

    def to_total_humidity(self, dry: Percent, wet: Percent) -> Percent | None:
        total = self.total
        return (self.dry * dry + self.wet * wet) / total if total > 0 else None

    def derated(self, dry_max: Positive, wet_max: Positive) -> Flows:
        if dry_max <= 0 or wet_max <= 0:
            raise ValueError("Max flows must be > 0")
        efforts = self.to_efforts(dry_max, wet_max)
        return self / max(efforts.dry, efforts.wet, 1.0)


@dataclass(slots=True, frozen=True)
class Efforts(DryWet[Normalised]):
    @property
    def overdriven(self) -> bool:
        return self.dry > 1.0 or self.wet > 1.0

    def derated(self) -> tuple[Efforts, float | None]:
        scale = max(self.dry, self.wet, 1.0)
        if scale <= 1.0:
            return self, None
        return Efforts(self.dry / scale, self.wet / scale), 1 / scale

    def to_flows(self, dry_max: Positive, wet_max: Positive) -> Flows:
        return Flows(self.dry * dry_max, self.wet * wet_max)


class MaxFlows(DryWet[Positive]):
    pass


@dataclass(slots=True, frozen=True)
class PumpOutput:
    effort: Normalised
    flow: NonNegative


@dataclass(slots=True, frozen=True)
class PumpsOutput:
    efforts: Efforts
    flows: Flows


@dataclass(slots=True, frozen=True)
class Blend:
    wet_fraction: Normalised
    flow: NonNegative


@dataclass(slots=True, frozen=True)
class PumpsSpec:
    max_flows: MaxFlows
    full_range_max_flow: Positive
    units: str | None = None


@dataclass(slots=True, frozen=True)
class PumpsView:
    flows: Flows
    efforts: Efforts
    max_flows: MaxFlows
    full_range_max_flow: Positive
    units: str | None = None

    @classmethod
    def from_spec_output(cls, spec: PumpsSpec, output: PumpsOutput) -> PumpsView:
        return cls(
            flows=output.flows,
            efforts=output.efforts,
            max_flows=spec.max_flows,
            full_range_max_flow=spec.full_range_max_flow,
            units=spec.units,
        )
