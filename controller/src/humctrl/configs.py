from dataclasses import dataclass
from typing import Self

from humctrl.config import Config, resolve
from humctrl.controller import Tuning
from humctrl.controller.types import ControlLawLike
from humctrl.pumps import DualPumps
from humctrl.rig import Rig
from humctrl.typing import Percent, Positive


@dataclass(slots=True)
class RigConfig(Config[Rig]):
    process_interval: Positive = 1.0
    pumps: Config[DualPumps] | None = None
    tunings: list[Tuning] | None = None
    tuning: str | ControlLawLike = None
    dry_humidity: Percent = 0
    wet_humidity: Percent = 100

    def build(self) -> Rig:
        return Rig(
            pumps=resolve(self.pumps),
            tunings=self.tunings,
            tuning=self.tuning,
            process_interval=self.process_interval,
            dry_humidity=self.dry_humidity,
            wet_humidity=self.wet_humidity,
        )

    def add_process_time(self, time: Positive) -> Self:
        self.process_time = time
        return self

    def add_pumps(self, pumps: Config[DualPumps] | None) -> Self:
        self.pumps = pumps
        return self

    def add_tunings(self, tunings: list[Tuning] | None) -> Self:
        self.tunings = tunings
        return self

    def add_default_tuning(self, default_tuning: str | None) -> Self:
        self.default_tuning = default_tuning
        return self

    def add_dry_humidity(self, dry_humidity: Percent) -> Self:
        self.dry_humidity = dry_humidity
        return self

    def add_wet_humidity(self, wet_humidity: Percent) -> Self:
        self.wet_humidity = wet_humidity
        return self
