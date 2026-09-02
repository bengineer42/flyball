from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Self, overload

from humctrl.controller import ControlLawConfig
from humctrl.manager import Manager
from humctrl.pumps import DualPumps
from humctrl.typing import Percent, Positive


class Config[T](ABC):
    @abstractmethod
    def build(self) -> T: ...


type ConfigOr[T] = Config[T] | T


@overload
def resolve[T](config: Config[T]) -> T: ...
@overload
def resolve[T](config: Config[T] | None) -> T | None: ...
@overload
def resolve[T](config: ConfigOr[T]) -> T: ...
def resolve[T](config: Config[T] | T | None) -> T | None:
    if isinstance(config, Config):
        return config.build()
    return config


@dataclass(slots=True)
class ManagerConfig(Config[Manager]):
    process_time: Positive = 1.0
    pumps: Config[DualPumps] | None = None
    control_law: ControlLawConfig | None = None
    dry_humidity: Percent = 0
    wet_humidity: Percent = 100

    def build(self) -> Manager:
        return Manager(
            pumps=resolve(self.pumps),
            control_law=resolve(self.control_law),
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
