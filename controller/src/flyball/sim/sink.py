from flyball.core.sink import Actuator, ActuatorState


class RecordingActuator(Actuator):
    """Takes demands and remembers them. For loops under test and rigs with nothing to drive."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.demands: list[float] = []
        self.applied = 0

    def set_demand(self, demand: float) -> float | None:
        self.demands.append(demand)
        return None

    def apply(self) -> None:
        self.applied += 1

    @property
    def state(self) -> ActuatorState:
        return ActuatorState(demand=self.demands[-1] if self.demands else None)
