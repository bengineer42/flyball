from collections.abc import Callable, Iterable, Mapping

from flyball.core.reading import Measurand, Reader, Sample, Source

Model = Callable[[int], Mapping[Measurand, float]]


class FunctionReader(Reader):
    """Polls a function as if it were a sensor.

    One model per source: `model(time_ns)` returns every measurand's value.
    `read` stamps them all with the time given, like a real bank behind a mux.
    """

    def __init__(self, name: str, models: Mapping[Source, Model]) -> None:
        super().__init__(name, models)
        self._models = dict(models)

    def read(self, time_ns: int) -> Iterable[Sample]:
        return [
            Sample(source, source.next_seq(), time_ns, dict(model(time_ns)))
            for source, model in self._models.items()
        ]
