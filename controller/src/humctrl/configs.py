from dataclasses import dataclass
from typing import Self

from humctrl.config import Config, resolve
from humctrl.controller import Tuning
from humctrl.controller.types import ControlLawConfig
from humctrl.manager import Manager
from humctrl.pumps import DualPumps
from humctrl.typing import Percent, Positive


@dataclass(slots=True)
class ManagerConfig(Config[Manager]):
    process_time: Positive = 1.0
    pumps: Config[DualPumps] | None = None
    tunings: list[Tuning] | None = None
    default_tuning: str | None = None
    dry_humidity: Percent = 0
    wet_humidity: Percent = 100

    def build(self) -> Manager:
        return Manager(
            pumps=resolve(self.pumps),
            tunings=self.tunings,
            default_tuning=self.default_tuning,
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
