from humctrl.pumps import Flows
from humctrl.pumps.types import DryWet
from humctrl.typing import Normalised, Percent

from .errors import PumpHumiditiesError
from .types import Rail


def expected_humidity_from_fraction(
    dry_humidity: Percent, wet_humidity: Percent, wet_fraction: Normalised
) -> Percent:
    return dry_humidity + wet_fraction * (wet_humidity - dry_humidity)


def get_expected_humidity_from_flows(flows: Flows, humidities: DryWet[Percent]) -> Percent | None:
    total = flows.total
    return (flows * humidities).sum() / total if total != 0.0 else None


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
