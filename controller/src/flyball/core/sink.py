from .config import Config
from .reading import Channel, Reading, Sample, Source


class Sink:
    """Takes something in, commits on ``apply``. Base for actuators and buffering observers.

    ``name`` identifies it in the rig, on the wire and in a recording -- for
    an actuator it is also the name of the loop driving it.
    """

    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def apply(self) -> None:
        """Commit whatever was handed over since the last apply. Default: nothing."""


class Observer[S: Sample | Reading]:
    """Hears samples from the sources and channels it names.

    ``observe`` is called once per sample whose source, or any of whose
    channels, is in ``observes`` -- an observer keyed on a channel still gets
    the whole sample and picks its measurand. ``touches`` lists the sinks the
    rig should ``apply`` after a delivery in which this observer fired.
    """

    name: str
    observes: frozenset[Source | Channel]
    touches: frozenset[Sink] = frozenset()

    def observe(self, sample: S) -> None:
        raise NotImplementedError


class ActuatorConfig[A: Actuator](Config[A]):
    def build(self) -> A: ...


class ActuatorState: ...


class Actuator(Sink):
    demand_units: str | None

    def set_demand(self, demand: float) -> float | None:
        raise NotImplementedError

    def state(self) -> ActuatorState:
        raise NotImplementedError

    def config(self) -> ActuatorConfig:
        raise NotImplementedError


def register_actuator(
    actuator: Actuator,
    state: type[ActuatorState],
) -> None:
    pass
