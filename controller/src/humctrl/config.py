from dataclasses import dataclass
from typing import Self

from humctrl.control_law import ControlLawConfig
from humctrl.manager import Manager
from humctrl.pumps import DualPumps
from humctrl.typing import Percent, Positive
from humctrl.utils import Config


def build_or_none[T](config: Config[T] | None) -> T | None:
    if config is None:
        return None
    return config.build()


@dataclass(slots=True)
class ManagerConfig(Config[Manager]):
    process_time: Positive = 1.0
    pumps: Config[DualPumps] | None = None
    control_law: ControlLawConfig | None = None
    dry_humidity: Percent = 0
    wet_humidity: Percent = 100

    def build(self) -> Manager:
        pumps = build_or_none(self.pumps)
        control_law = build_or_none(self.control_law)
        return Manager(
            pumps=pumps,
            control_law=control_law,
            process_time=self.process_time,
            dry_humidity=self.dry_humidity,
            wet_humidity=self.wet_humidity,
        )

    def add_process_time(self, time: Positive) -> Self:
        self.process_time = time
        return self

    def add_pumps(self, pumps: Config[DualPumps] | None) -> Self:
        self.pumps = pumps
        return self

    def add_control_law(self, control_law: ControlLawConfig | None) -> Self:
        self.control_law = control_law
        return self

    def add_dry_humidity(self, dry_humidity: Percent) -> Self:
        self.dry_humidity = dry_humidity
        return self

    def add_wet_humidity(self, wet_humidity: Percent) -> Self:
        self.wet_humidity = wet_humidity
        return self
