from dataclasses import dataclass
from enum import Enum

from humctrl.manager import PumpHumiditiesError, TargetHumidityNotSetError
from humctrl.pumps import AbsoluteFlows, DualPumps, OnOverdrive
from humctrl.typing import NonNegative, Normalised, Percent


class HumidityRailError(ValueError):
    """Raised when the wet and dry humidities are not in the expected order."""

    def __init__(self, wet_humidity: Percent, dry_humidity: Percent, target_humidity: Percent):
        super().__init__(
            f"Wet humidity ({wet_humidity}) must be greater than dry humidity ({dry_humidity}) "
            f"for target humidity ({target_humidity})"
        )


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
    wet_humidity: Percent, dry_humidity: Percent, wet_fraction: Normalised
) -> Percent:
    return dry_humidity + wet_fraction * (wet_humidity - dry_humidity)


def get_expected_humidity_from_flows(
    wet_humidity: Percent, dry_humidity: Percent, wet_flow: NonNegative, dry_flow: NonNegative
) -> Percent:
    return (wet_flow * wet_humidity + dry_flow * dry_humidity) / (wet_flow + dry_flow)


def calculate_fraction(
    wet_humidity: Percent, dry_humidity: Percent, target: Percent
) -> Normalised | Rail:

    if wet_humidity <= dry_humidity:
        raise PumpHumiditiesError()
    if target < dry_humidity:
        return Rail.DRY
    if target > wet_humidity:
        return Rail.WET
    return (target - dry_humidity) / (wet_humidity - dry_humidity)


@dataclass(frozen=True, slots=True)
class Absolute:
    value: NonNegative
    on_overdrive: OnOverdrive = OnOverdrive.RAISE


@dataclass(frozen=True, slots=True)
class OfBlendMax:
    value: NonNegative


@dataclass(frozen=True, slots=True)
class OfFullRangeMax:
    value: NonNegative


class OnRail(Enum):
    RAISE = "raise"  # raise FlowsOverdrivenError
    CLAMP = "clamp"  # clamp to the maximum flow


type BlendFlow = Absolute | OfBlendMax | OfFullRangeMax


class Blender:
    pumps: DualPumps
    wet_humidity: Percent
    dry_humidity: Percent

    _target_humidity: Percent
    _target_flow: BlendFlow = OfBlendMax(1.0)
    _on_rail: OnOverdrive = OnOverdrive.CLAMP

    def __init__(self, pumps: DualPumps, wet_humidity: Percent, dry_humidity: Percent):
        self.pumps = pumps
        self.wet_humidity = wet_humidity
        self.dry_humidity = dry_humidity
        self._target_flow = OfFullRangeMax(1.0)
        self._on_rail = OnOverdrive.CLAMP

    def humidity_to_fraction(self, humidity: Percent) -> Normalised | Rail:
        return calculate_fraction(self.wet_humidity, self.dry_humidity, humidity)

    def raise_humidity_rail_error(self, target_humidity: Percent) -> None:
        raise HumidityRailError(self.wet_humidity, self.dry_humidity, target_humidity)

    def update_stream(
        self,
        flow: BlendFlow | None = None,
        humidity: Percent | None = None,
        wet_humidity: Percent | None = None,
        dry_humidity: Percent | None = None,
        on_rail: OnOverdrive | None = None,
    ) -> AbsoluteFlows:

        if humidity is not None:
            self._target_humidity = humidity
        if flow is not None:
            self._target_flow = flow
        if on_rail is not None:
            self._on_rail = on_rail
        if wet_humidity is not None:
            self.wet_humidity = wet_humidity
        if dry_humidity is not None:
            self.dry_humidity = dry_humidity

        assert self._target_humidity is not None, TargetHumidityNotSetError()

        wet_fraction = self.humidity_to_fraction(self._target_humidity)
        if isinstance(wet_fraction, Rail) and on_rail == OnOverdrive.RAISE:
            self.raise_humidity_rail_error(self._target_humidity)

        return self.pumps.set_blend(flow, float(wet_fraction))

    def update_humidity(self, humidity: Percent) -> AbsoluteFlows:
        self._target_humidity = humidity
        return self.update_stream(humidity=humidity)
