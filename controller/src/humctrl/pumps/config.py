from . import DualPumps
from .drivers import DualPumpDriver


class DualPumpsConfig(Config[DualPumps]):
    units: str | None = None
    max_flows: MaxFlows
    driver: ConfigOr[DualPumpDriver]

    def build(self) -> DualPumps:
        return DualPumps(
            pumps=resolve(self.driver),
            units=self.units,
            max_flows=self.max_flows,
        )
