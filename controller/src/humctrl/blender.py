from dataclasses import dataclass
from enum import Enum

from humctrl.manager import PumpHumiditiesError, TargetHumidityNotSetError
from humctrl.pumps import (
    Absolute,
    AbsoluteFlows,
    BlendFlow,
    DualPumps,
    OfBlendMax,
    OfFullRangeMax,
    OnOverdrive,
)
from humctrl.typing import NonNegative, Normalised, Percent


class HumidityRailError(ValueError):
    """Raised when the wet and dry humidities are not in the expected order."""

    def __init__(self, wet_humidity: Percent, dry_humidity: Percent, target_humidity: Percent):
        super().__init__(
            f"Wet humidity ({wet_humidity}) must be greater than dry humidity ({dry_humidity}) "
            f"for target humidity ({target_humidity})"
        )


def expected_humidity_from_fraction(
    dry_humidity: Percent, wet_humidity: Percent, wet_fraction: Normalised
) -> Percent:
    return dry_humidity + wet_fraction * (wet_humidity - dry_humidity)


def get_expected_humidity_from_flows(
    wet_humidity: Percent, dry_humidity: Percent, wet_flow: NonNegative, dry_flow: NonNegative
) -> Percent:
    return (wet_flow * wet_humidity + dry_flow * dry_humidity) / (wet_flow + dry_flow)


class Rail(Enum):
    WET = "wet"
    DRY = "dry"

    def __float__(self) -> float:
        match self:
            case Rail.WET:
                return 1.0
            case Rail.DRY:
                return 0.0


def calculate_wet_fraction(
    dry_humidity: Percent, wet_humidity: Percent, target: Percent
) -> Normalised | Rail:

    if wet_humidity <= dry_humidity:
        raise PumpHumiditiesError()
    if target < dry_humidity:
        return Rail.DRY
    if target > wet_humidity:
        return Rail.WET
    return (target - dry_humidity) / (wet_humidity - dry_humidity)


# class Blender:
#     wet: Percent
#     dry: Percent
#     output: Percent
#     flow: BlendFlow

#     def __init__(
#         self,
#         wet: Percent,
#         dry: Percent,
#         output: Percent,
#         flow: BlendFlow,
#     ):
#         self.wet = wet
#         self.dry = dry
#         self.output = output
#         self.flow = flow

#     def humidity_to_fraction(self) -> Normalised | Rail:
#         return calculate_wet_fraction(self.dry, self.wet, self.output)

#     def update(
#         self,
#         wet: Percent | None = None,
#         dry: Percent | None = None,
#         output: Percent | None = None,
#         flow: BlendFlow | None = None,
#     ) -> AbsoluteFlows:

#         if humidity is not None:
#             self._target_humidity = humidity
#         if wet_humidity is not None:
#             self.wet_humidity = wet_humidity
#         if dry_humidity is not None:
#             self.dry_humidity = dry_humidity
#         if self._target_humidity is None:
#             raise TargetHumidityNotSetError()

#         wet_fraction = self.humidity_to_fraction(self._target_humidity)
#         if isinstance(wet_fraction, Rail) and on_rail == OnOverdrive.RAISE:
#             self.raise_humidity_rail_error(self._target_humidity)

#     def update_target_blend(self, wet: NonNegative, dry: NonNegative):
#         total = wet + dry
#         if total > 0:
#             self._target_humidity = wet / total
#         self._target_total_flow = Absolute(total)
