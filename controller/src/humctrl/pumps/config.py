from humctrl.core.config import Config, ConfigOr, resolve
from humctrl.pumps.types import MaxFlowsDefault, MaxFlowsLike

from .drivers import DualPumpDriver
from .dual import DualPumps


class DualPumpsConfig(Config[DualPumps]):
    units: str | None = None
    max_flows: MaxFlowsLike = MaxFlowsDefault
    driver: ConfigOr[DualPumpDriver]

    def build(self) -> DualPumps:
        return DualPumps(
            pumps=resolve(self.driver),
            units=self.units,
            max_flows=self.max_flows,
        )
