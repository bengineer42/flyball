from __future__ import annotations

from collections.abc import Iterable, Sequence
from threading import RLock
from typing import Any, Protocol

from flyball.control import ControlLawLike, Loop, LoopState, Tunings
from flyball.core import Clock
from flyball.core.errors import ConflictError
from flyball.core.reading import Channel, Reader, Reading, Sample, Source
from flyball.core.sink import RESERVED_NAMES, Actuator, ActuatorState, Observer, Sink
from flyball.core.topic import Latest
from flyball.core.typing import OrderedSet
from flyball.db import Store
from flyball.runtime.reader import Readers
from flyball.runtime.signals import Signals

from .loops import Loops
from .recorder import Recorder


class Rig(Protocol):
    clock: Clock
    loops: Loops
    actuators: dict[str, Actuator]
    lock: RLock
    #: The newest state of each actuator, by name: after a tick applied to it
    #: or a command ran on it. A snapshot, built only while someone watches,
    #: so an idle rig pays a bool check per apply and a watched one a dict store.
    actuator_states: Latest[str, ActuatorState]
    #: The newest state of each loop, by name, after each tick. Same terms.
    #: State, not view: the spec (law config, offset) changes only on a
    #: retune, and a reader joins it on at its own rate.
    loop_states: Latest[str, LoopState]
    #: What is being waited on, by name: prompts, settle tests, holds.
    signals: Signals
    observations: dict[Source | Channel, OrderedSet[Observer]]
    tunings: Tunings
    recorder: Recorder | None
    _readers: Readers
    _samples: dict[Source, Sample]
    _readings: dict[Channel, Reading]

    def __init__(self) -> None:
        self.clock = Clock()
        self.loops = Loops()
        self.actuators = {}
        self.lock = RLock()
        self.actuator_states = Latest()
        self.loop_states = Latest()
        self.signals = Signals(self.clock)
        self.observations = {}
        self.tunings = Tunings()
        self.recorder = None
        self._readers = Readers(self)
        self._samples = {}
        self._readings = {}

    @property
    def sources(self) -> set[Source]:
        """Every source the rig can hear: from its readers, its loops, and anything already seen."""
        return (
            {source for reader in self._readers.by_name.values() for source in reader.sources}
            | {channel.source for channel, _ in self.loops.entries()}
            | set(self._samples)
        )

    def attach_observer(self, observer: Observer) -> None:
        """Register under every source and channel the observer asked for."""
        for key in observer.observes:
            self.observations.setdefault(key, {})[observer] = None

    def detach_observer(self, observer: Observer) -> None:
        for key in observer.observes:
            if (observers := self.observations.get(key)) is not None:
                observers.pop(observer, None)

    def add_actuator(self, actuator: Actuator) -> None:
        """Make an actuator reachable by name, for commands. Loops add theirs."""
        if actuator.name in RESERVED_NAMES:
            raise ConflictError(f"Actuator name {actuator.name!r} is reserved as a route segment")
        if (existing := self.actuators.get(actuator.name)) is not None and existing is not actuator:
            raise ConflictError(f"Actuator {actuator.name!r} is already attached")
        self.actuators[actuator.name] = actuator

    def apply(self, sink: Sink) -> None:
        """Commit a sink and, for an actuator, note what it is doing now for anyone watching."""
        sink.apply()
        if isinstance(sink, Actuator) and self.actuator_states.watched:
            self.actuator_states.set(sink.name, sink.state)

    def start_reader(self, reader: Reader, period: float, stop_on_error: bool = True) -> None:
        self._readers.start_periodic(reader, period)

    @property
    def readers(self) -> Readers:
        return self._readers

    def attach_loop(
        self,
        channel: Channel,
        actuator: Actuator,
        law: ControlLawLike | str | None = None,
        default: bool = False,
    ) -> None:
        if isinstance(law, str):
            law = self.tunings.get(law)
        self.add_actuator(actuator)
        self.loops.add(channel, Loop(self.clock, actuator, law=law), default=default)

    # region Recording

    def start_recording(
        self,
        store: Store,
        sources: Iterable[Source] | None = None,
        loops: Iterable[tuple[Channel, Loop[Any]]] | None = None,
        **session: Any,
    ) -> Recorder:
        """Open a session and record into it from the next delivery on.

        Defaults to every source seen so far and every loop. Replaces a
        recorder already running, closing its session first. ``session`` is
        passed to ``Store.open_session`` -- config, hardware, version.
        """
        with self.lock:
            self.stop_recording()
            writer = store.open_session(self.clock.now_ns(), **session)
            self.recorder = Recorder(
                writer,
                self.sources if sources is None else sources,
                self.loops.entries() if loops is None else loops,
            )
            return self.recorder

    def stop_recording(self) -> None:
        with self.lock:
            if (recorder := self.recorder) is not None:
                self.recorder = None
                recorder.close(self.clock.now_ns())

    # endregion

    def read(self, reader: Reader) -> None:
        with self.lock:
            self.on_read(tuple(reader.read(self.clock.now_ns())))

    def on_read(self, samples: Sequence[Sample]) -> None:
        """One delivery: observers, then the loops, one apply per touched sink, then the recorder.

        The recorder goes last so it sees what each tick produced.
        """
        processes: list[tuple[Loop[Any], Reading]] = []
        touched: set[Sink] = set()
        for sample in samples:
            self._samples[sample.source] = sample
            for observer in self.observations.get(sample.source, ()):
                observer.observe(sample)
                touched.update(observer.touches)
            for measurand in sample.values:
                channel = sample.source[measurand]
                reading = sample.reading(measurand)
                self._readings[channel] = reading
                if channel in self.observations:
                    for observer in self.observations[channel]:
                        observer.observe(reading)
                        touched.update(observer.touches)
                if (loop := self.loops.find(channel)) is not None:
                    processes.append((loop, reading))

        for loop, reading in processes:
            loop.tick(reading)
            touched.add(loop.actuator)
            if self.loop_states.watched:
                self.loop_states.set(loop.name, loop.state)
        for sink in touched:
            self.apply(sink)
        if self.recorder is not None:
            self.recorder.record(samples, processes)
