from collections.abc import Callable, Iterable, Mapping

from flyball.core.reading import Measurand, Reader, Sample, Source

Model = Callable[[int], Mapping[Measurand, float]]


class FunctionReader(Reader):
    """Polls a function as if it were a sensor.

    One model per source: ``model(time_ns)`` returns the values of every
    measurand that source declares. ``read`` calls each in turn and stamps
    them all with the time it was given, so a bank of simulated sensors shares
    one instant like a real bank behind a mux does.
    """

    def __init__(self, name: str, models: Mapping[Source, Model]) -> None:
        super().__init__(name, models)
        self._models = dict(models)
        self._seq = 0

    def read(self, time_ns: int) -> Iterable[Sample]:
        self._seq += 1
        return [
            Sample(source, self._seq, time_ns, dict(model(time_ns)))
            for source, model in self._models.items()
        ]
