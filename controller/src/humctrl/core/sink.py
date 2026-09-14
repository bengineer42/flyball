from .reading import Channel, Reading, Sample, Source


class Sink:
    """Takes something in, commits on ``apply``. Base for actuators and buffering observers."""

    def apply(self) -> None:
        """Commit whatever was handed over since the last apply. Default: nothing."""


class Observer:
    """Hears samples from the sources and channels it names.

    ``observe`` is called once per sample whose source, or any of whose
    channels, is in ``observes`` -- an observer keyed on a channel still gets
    the whole sample and picks its quantity. ``touches`` lists the sinks the
    rig should ``apply`` after a delivery in which this observer fired.
    """

    observes: frozenset[Source | Channel]
    touches: frozenset[Sink] = frozenset()

    def observe(self, sample: Sample | Reading) -> None:
        raise NotImplementedError


class Actuator(Sink):
    def set_demand(self, demand: float) -> float | None:
        raise NotImplementedError
