from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from threading import RLock
from typing import Any, overload

from flyball.control import ControlLawLike, Controller, Loop, LoopState, Tunings
from flyball.control.feedforward import (
    FeedforwardConfig,
    FeedforwardLike,
    Feedforwards,
    NoFeedforward,
    Setpoint,
)
from flyball.core import Clock
from flyball.core.device import Condition, Device, Event, Level
from flyball.core.errors import ConflictError, NotReadyError
from flyball.core.reading import Channel, Reader, Reading, Sample, Source
from flyball.core.signal import Access, AddressNotFoundError, Node, Signal, WriteState
from flyball.core.signal import Reading as SignalReading
from flyball.core.signal import Sample as NodeSample
from flyball.core.sink import RESERVED_NAMES, Actuator, ActuatorState, Observer, Sink
from flyball.core.topic import Latest, Topic
from flyball.core.typing import OrderedSet
from flyball.db import Store
from flyball.runtime.reader import Readers
from flyball.runtime.triggers import Triggers
from flyball.runtime.writer import Writer, is_blocking

from .controllers import Controllers
from .loops import Loops
from .polling import Polling, poll_period
from .recorder import Recorder

log = logging.getLogger("flyball.rig")
RECENT_READINGS = 60
"""How many readings the rig keeps per channel, for a stat on request: noise, rate."""


class Rig:
    clock: Clock
    loops: Loops  # legacy: goes in step 5
    actuators: dict[str, Actuator]  # legacy: goes in step 3
    devices: dict[str, Device]
    """Every reader, actuator and application device, by name: one namespace rig-wide.

    A source is not a `Device` -- it has no config or commands -- but its
    name lives in the same namespace (a reader's source appears beside
    device names in the same UI and API), so it is claimed here too even
    though it is not a value in this dict. Links and plants are not: they
    are declared in their own `links` section of a rig file and addressed
    through their own routes (`/api/sim/plants/{name}`), not alongside
    devices, so a link and a device may share a name.
    """
    name: str | None
    """What the rig file called it, if it came from one."""
    links: dict[str, Any]
    """What the rig file's `links` built, by name: buses, sessions, simulated plants."""
    writers: dict[str, Writer]  # legacy: goes in step 3
    """A thread per blocking actuator, carrying the loop's demands to the bus."""
    lock: RLock
    actuator_states: Latest[str, ActuatorState]  # legacy: goes in step 5
    """The newest state of each actuator, by name. Built only while someone watches."""
    loop_states: Latest[str, LoopState]  # legacy: goes in step 5
    """The newest state of each loop, by name, after each tick. A reader joins the spec itself."""
    triggers: Triggers
    """What is being waited on, by name: prompts, settle tests, holds."""
    events: Topic[Event]
    """Everything that happened, as it happens."""
    recent: deque[Event]
    """The last few hundred events, for a late joiner."""
    observations: dict[Source | Channel, OrderedSet[Observer]]  # legacy: goes in step 3
    tunings: Tunings
    recorder: Recorder | None
    _readers: Readers  # legacy: goes in step 3
    _samples: dict[Source, Sample]  # legacy: goes in step 3
    _readings: dict[Channel, Reading]  # legacy: goes in step 3
    _recent: dict[Channel, deque[Reading]]  # legacy: goes in step 3
    _claims: dict[str, tuple[str, object]]
    """Every name claimed rig-wide, with who claimed it and as what -- devices and sources
    both, so a collision can be reported before it does anything harder to undo."""
    # The device model, beside the legacy paths above until the drivers move over.
    controllers: Controllers
    polling: Polling
    latest: dict[Signal, SignalReading]
    """The last reading delivered on each signal."""
    samples: Latest[str, NodeSample]
    """The newest sample per node address, for a watcher. Built only while someone watches."""
    write_states: Latest[str, WriteState]
    """The newest write state per signal address, after each commit. Likewise."""
    controller_states: Latest[str, LoopState]
    """The newest state of each controller, by name, after each tick. Likewise."""
    _last_samples: dict[Node, NodeSample]
    """The last sample delivered on each node."""
    _last_cut: dict[Node, NodeSample]
    """The last sample delivered on another node of the tree, cut down to this atomic one: a
    root sample carrying `dry.humidity` is the newest instant on `dry` too."""
    _recent_signal: dict[Signal, deque[SignalReading]]
    _observers: dict[Signal, OrderedSet[Device]]
    """Devices with a bound input on a signal, by that signal."""
    _node_observers: dict[Node, OrderedSet[Device]]
    """Devices bound to a whole node, by that node: they get each sample cut down to it."""
    _requested: dict[Signal, float]
    """What a demand asked for where the clamp changed it, until the commit reports it."""
    _touched: dict[Device, None] | None
    """The devices the delivery in progress applied to or observed on; None outside one."""

    def __init__(self, name: str | None = None) -> None:
        self.name = name
        self.links = {}
        self.writers = {}
        self.clock = Clock()
        self.loops = Loops()
        self.actuators = {}
        self.devices = {}
        self._claims = {}
        self.lock = RLock()
        self.actuator_states = Latest()
        self.loop_states = Latest()
        self.triggers = Triggers(self.clock)
        self.events = Topic()
        self.recent = deque(maxlen=500)
        self.observations = {}
        self.tunings = Tunings()
        self.recorder = None
        self._readers = Readers(self)
        self._samples = {}
        self._readings = {}
        self._recent = {}
        self.controllers = Controllers()
        self.polling = Polling(self)
        self.latest = {}
        self.samples = Latest()
        self.write_states = Latest()
        self.controller_states = Latest()
        self._last_samples = {}
        self._last_cut = {}
        self._recent_signal = {}
        self._observers = {}
        self._node_observers = {}
        self._requested = {}
        self._touched = None

    # legacy: goes in step 3
    def reading(self, channel: Channel) -> Reading | None:
        """The last reading delivered on `channel`, if any."""
        return self._readings.get(channel)

    # legacy: goes in step 3
    def recent_readings(self, channel: Channel, n: int = RECENT_READINGS) -> list[Reading]:
        """The last `n` readings on `channel`, oldest first: a copy, so compute on it unlocked."""
        with self.lock:
            recent = self._recent.get(channel)
            if recent is None:
                return []
            return list(recent)[-n:]

    # legacy: goes in step 3
    @property
    def sources(self) -> set[Source]:
        """Every source the rig can hear: from its readers, its loops, and anything already seen."""
        return (
            {source for reader in self._readers.by_name.values() for source in reader.sources}
            | {channel.source for channel, _ in self.loops.entries()}
            | set(self._samples)
        )

    # legacy: goes in step 3
    def attach_observer(self, observer: Observer) -> None:
        """Register under every source and channel the observer asked for."""
        for key in observer.observes:
            self.observations.setdefault(key, {})[observer] = None

    # legacy: goes in step 3
    def detach_observer(self, observer: Observer) -> None:
        for key in observer.observes:
            if (observers := self.observations.get(key)) is not None:
                observers.pop(observer, None)

    def claim(self, name: str, kind: str, owner: object) -> None:
        """Reserve `name` for `kind` (a reader, an actuator, a source, ...) rig-wide.

        Re-claiming `name` for the same `owner` is a no-op -- a loop may
        reattach an actuator it already added. Claiming it for anything else
        is a collision: no reader, actuator, source or application device
        may share a name with another, regardless of kind.

        Raises:
            ConflictError: `name` is reserved as a route segment, or already
                claimed by something else.
        """
        if name in RESERVED_NAMES:
            raise ConflictError(f"Name {name!r} is reserved as a route segment")
        existing = self._claims.get(name)
        if existing is not None:
            existing_kind, existing_owner = existing
            if existing_owner is owner:
                return
            raise ConflictError(
                f"Name {name!r} is already used by {existing_kind} {name!r}"
                f" (cannot also be {kind} {name!r})"
            )
        self._claims[name] = (kind, owner)

    def release(self, name: str) -> None:
        """Free a claimed name, e.g. when an application detaches its own device. For tests too."""
        self._claims.pop(name, None)
        self.devices.pop(name, None)

    def kind_of(self, name: str) -> str | None:
        """What claimed `name` -- "reader", "actuator", "source", "simulation", ... -- or None."""
        claim = self._claims.get(name)
        return None if claim is None else claim[0]

    # legacy: goes in step 3
    def add_actuator(self, actuator: Actuator) -> None:
        """Make an actuator reachable by name, for commands. Loops add theirs."""
        self.claim(actuator.name, "actuator", actuator)
        self.devices[actuator.name] = actuator
        self.actuators[actuator.name] = actuator

    def event(
        self,
        level: Level,
        scope: str,
        subject: str,
        kind: str,
        message: str,
        details: Any = None,
    ) -> Event:
        """Record that something happened: logged, kept, pushed to watchers, recorded."""
        event = Event(self.clock.now_ns(), level, scope, subject, kind, message, details)
        log.log(int(level), "%s %s: %s", scope, subject, message)
        self.recent.append(event)
        self.events.publish(event)
        if self.recorder is not None:
            self.recorder.event(event)
        return event

    # legacy: goes in step 3
    def _writer_for(self, actuator: Actuator) -> Callable[[float], float | None] | None:
        """A queue in front of an actuator that blocks on a bus; None for one that does not."""
        if not is_blocking(actuator):
            return None
        if (writer := self.writers.get(actuator.name)) is None:
            writer = self.writers[actuator.name] = Writer(self, actuator)
        return writer.request

    # legacy: goes in step 3
    def write_conditions(self) -> list[tuple[str, Condition]]:
        """Bus failures the writers are seeing now, by actuator name."""
        return [(name, w.failed) for name, w in self.writers.items() if w.failed is not None]

    def stop(self) -> None:
        """Stop what runs on threads: polling, writers, recording. The rig can be built again."""
        self._readers.stop_all()
        self.polling.stop_all()
        for writer in self.writers.values():
            writer.stop()
        self.stop_recording()

    # legacy: goes in step 3
    def apply(self, sink: Sink) -> None:
        """Commit a sink and, for an actuator, note what it is doing now for anyone watching."""
        sink.apply()
        if isinstance(sink, Actuator) and self.actuator_states.watched:
            self.actuator_states.set(sink.name, sink.state)

    # legacy: goes in step 3
    def start_reader(self, reader: Reader, period: float | None = None) -> None:
        """Attach a reader; with a `period`, poll it too. A push-only reader needs none."""
        if period is None:
            self._readers.add(reader)
        else:
            self._readers.start_periodic(reader, period)

    # legacy: goes in step 3
    @property
    def readers(self) -> Readers:
        return self._readers

    # legacy: goes in step 5
    def attach_loop(
        self,
        channel: Channel,
        actuator: Actuator,
        law: ControlLawLike | str | None = None,
        default: bool = False,
        min_period_s: float | None = None,
        feedforward: FeedforwardLike | str | None = None,
    ) -> None:
        """Regulate `channel` through `actuator`.

        Args:
            channel: The controlled variable.
            actuator: What the loop drives; added to the rig if new.
            law: The control law, a config, or a stored tuning's name.
            default: Make this the loop commands address when they name none.
            min_period_s: Step the law at most this often.
            feedforward: What maps the setpoint to a demand in the actuator's
                unit: an instance, a config, or a tag. Default: the setpoint
                itself when the actuator takes the channel's unit, else none
                (the law does all the work).
        """
        if isinstance(law, str):
            law = self.tunings.get(law)
        demand_unit = actuator.demand_unit  # an instance may narrow the class's
        same_unit = demand_unit is None or demand_unit == channel.unit
        if isinstance(feedforward, str):
            feedforward = Feedforwards[feedforward]()
        elif isinstance(feedforward, FeedforwardConfig):
            feedforward = feedforward.build()
        if feedforward is None:
            feedforward = Setpoint() if same_unit else NoFeedforward()
        elif isinstance(feedforward, Setpoint) and not same_unit:
            # Handing an actuator demands in the channel's unit when it expects
            # another would run happily and do nonsense.
            raise ConflictError(
                f"loop on {channel.name} ({channel.unit}) cannot pass its setpoint to"
                f" {actuator.name!r}, which takes demands in {demand_unit}"
            )
        self.add_actuator(actuator)
        self.loops.add(
            channel,
            Loop(
                self.clock,
                actuator,
                law=law,
                min_period_s=min_period_s,
                write=self._writer_for(actuator),
                feedforward=feedforward,
            ),
            default=default,
        )

    # region Recording

    def start_recording(
        self,
        store: Store,
        sources: Iterable[Source] | None = None,
        loops: Iterable[tuple[Channel, Loop[Any]]] | None = None,
        **session: Any,
    ) -> Recorder:
        """Open a session and record into it from the next delivery on.

        Defaults to every source seen so far and every loop. Replaces a running
        recorder, closing its session first.
        """
        with self.lock:
            self.stop_recording()
            writer = store.open_session(self.clock.now_ns(), **session)
            self.recorder = Recorder(
                writer,
                self.sources if sources is None else sources,
                self.loops.entries() if loops is None else loops,
                on_failure=self._recording_failed,
            )
            return self.recorder

    def _recording_failed(self, error: Exception) -> None:
        """From the recorder's thread: detach it first, so the event does not go back to it."""
        with self.lock:
            recorder, self.recorder = self.recorder, None
        self.event(
            Level.ERROR,
            "rig",
            "recorder",
            "recording_failed",
            f"recording stopped: {type(error).__name__}: {error}",
        )
        if recorder is not None:
            with suppress(Exception):  # the store already failed once
                recorder.writer.end(self.clock.now_ns())

    def stop_recording(self) -> None:
        with self.lock:
            if (recorder := self.recorder) is not None:
                self.recorder = None
                recorder.close(self.clock.now_ns())

    # endregion

    # region The device model

    def add_device(self, device: Device) -> None:
        """Make a device reachable by name: one namespace with every reader, actuator and source.

        Raises:
            ConflictError: The name is reserved, or already something else's.
        """
        self.claim(device.name, "device", device)
        self.devices[device.name] = device

    def bind_inputs(self, device: Device, roles: Mapping[str, str]) -> None:
        """Resolve `device`'s bound inputs (`role -> address`) and follow them.

        Call after every device is added: an address may name a device
        declared later in the file. From then on, every reading on a bound
        signal reaches `device.observe` in the delivery it arrives in; for a
        bound node, every sample carrying something under it does, cut down
        to that, after the readings.

        Raises:
            AddressNotFoundError: An address does not resolve; names it.
            ConflictError: A signal that does not publish, or a node with
                nothing publishing under it.
        """
        for role, address in roles.items():
            target = self.resolve(address)
            if isinstance(target, Signal):
                if Access.P not in target.access:
                    raise ConflictError(
                        f"{device.name}.bound.{role}: '{target.address}' [{target.access}]"
                        " is not publishing"
                    )
                self._observers.setdefault(target, {})[device] = None
            else:
                if not any(Access.P in s.access for s in target.walk()):
                    raise ConflictError(
                        f"{device.name}.bound.{role}: nothing under '{target.address}' publishes"
                    )
                self._node_observers.setdefault(target, {})[device] = None
            device.bound[role] = target

    def resolve(self, address: str) -> Node | Signal:
        """Walk `address` from its device down to a namespace or a signal.

        The only place addresses are parsed: below here everything carries
        the bound objects.

        Raises:
            AddressNotFoundError: Naming the segment that failed and what
                it was looked for under.
        """
        name, dot, relative = address.partition(".")
        root = getattr(self.devices.get(name), "root", None)  # a legacy device has no tree
        if root is None:
            raise AddressNotFoundError(address, name, None)
        if dot and not relative:  # "hum." names nothing; "hum" is the root
            raise AddressNotFoundError(address, "", root.address)
        return root.find(relative)

    @overload
    def read(self, target: Signal, *, fresh: bool = False) -> SignalReading: ...
    @overload
    def read(self, target: Node, *, fresh: bool = False) -> NodeSample | Iterator[NodeSample]: ...
    @overload
    def read(self, target: Reader, *, fresh: bool = False) -> None: ...
    def read(
        self, target: Node | Signal | Reader, *, fresh: bool = False
    ) -> SignalReading | NodeSample | Iterator[NodeSample] | None:
        """The last read on `target`: a Signal's Reading, an atomic Node's Sample, else Samples.

        `fresh` reads the hardware first and delivers what comes back as a
        poll would, so the rig's state and what the caller sees agree: how a
        setting (`RW`, never published) is read. An atomic node's sample is
        the newest instant on it, whether delivered on it or cut from a
        sample on another node of the tree. A Reader (legacy) is polled
        once, now.

        Raises:
            ConflictError: The signal is not readable.
            NotReadyError: Nothing has been read on it yet.
        """
        if isinstance(target, Reader):
            return self._read_reader(target)  # legacy: goes in step 3
        node = target.node if isinstance(target, Signal) else target
        with self.lock:
            if fresh:
                samples = tuple(node.device.read(self.clock.now_ns(), node))
                self.on_samples(samples)
            if isinstance(target, Signal):
                if Access.R not in target.access:
                    raise ConflictError(f"'{target.address}' [{target.access}] is not readable")
                if (reading := self.latest.get(target)) is None:
                    raise NotReadyError(f"Nothing has been read on '{target.address}' yet")
                return reading
            if target.atomic:
                known = (self._last_samples.get(target), self._last_cut.get(target))
                if not (samples := [s for s in known if s is not None]):
                    raise NotReadyError(f"Nothing has been read on '{target.address}' yet")
                return max(samples, key=lambda s: s.time_ns)  # a tie: the one delivered on it
            nodes = (target, *target.descendants())
            return iter([s for n in nodes if (s := self._last_samples.get(n)) is not None])

    # legacy: goes in step 3
    def _read_reader(self, reader: Reader) -> None:
        """Poll `reader` once, now. An attached reader delivers through its own path."""
        samples = tuple(reader.read(self.clock.now_ns()))
        if reader.name in self._readers.by_name:
            reader.emit(samples)
        else:
            with self.lock:
                self.on_read(samples)

    def demand(
        self, node: Node, values: Mapping[str | Signal, float], *, by: Controller | None = None
    ) -> Mapping[Signal, WriteState]:
        """Put `values` on W signals under `node`, as one demand, in each signal's unit.

        A key is a bound signal under `node`, or its name relative to
        `node`, dotted for a namespace -- the wire's form, resolved here and
        nowhere below. The whole demand is checked before anything is
        recorded -- every key a W signal, no signal another controller's,
        every `together` group complete -- then clamped to `limits` and
        fanned out to `device.apply`. A manual demand (`by` None, or from
        outside a delivery) is committed now and its states returned; a
        controller's inside a delivery is committed with everything else at
        its end, and this returns nothing.

        Raises:
            AddressNotFoundError: A name does not resolve under `node`.
            ConflictError: A key is not a writable signal under `node`, is
                driven by a controller, or is set without its `together`
                siblings.
            ValueError: No values, or one signal named twice.
        """
        if not values:
            raise ValueError(f"Demand on '{node.address}' carries no values")
        device = node.device
        resolved: dict[Signal, float] = {}
        for key, value in values.items():
            if isinstance(key, Signal):
                if not node.contains(key):
                    raise ConflictError(f"'{key.address}' is not under '{node.address}'")
                signal = key
            else:
                found = node.find(key)
                if not isinstance(found, Signal):
                    raise ConflictError(f"'{found.address}' is a namespace, not a signal")
                signal = found
            if signal in resolved:
                raise ValueError(f"Demand on '{node.address}' names '{signal.address}' twice")
            resolved[signal] = float(value)
        clamped: dict[Signal, float] = {}
        requested: dict[Signal, float] = {}
        for signal, value in resolved.items():
            if Access.W not in signal.access:
                raise ConflictError(f"'{signal.address}' [{signal.access}] is not writable")
            if (holder := self.controllers.driving(signal)) is not None and holder is not by:
                raise ConflictError(
                    f"'{signal.address}' is driven by controller {holder.name!r}:"
                    " set its reference, or detach it"
                )
            if (limits := signal.limits) is not None:
                held = min(max(value, limits[0]), limits[1])
                if held != value:
                    requested[signal] = value
                value = held
            clamped[signal] = value
        for signal in clamped:
            # `together` is names in the spec; each resolves among its siblings.
            missing = [
                name
                for name in sorted(signal.spec.together)
                if signal.node.signals.get(name) not in clamped
            ]
            if missing:
                raise ConflictError(f"'{signal.address}' is set with {', '.join(missing)}")
        with self.lock:
            time_ns = self.clock.now_ns()
            for signal, value in clamped.items():
                device.apply(signal, time_ns, value)
            self._requested.update(requested)
            if by is not None and self._touched is not None:
                self._touched[device] = None
                return {}
            return self._commit((device,), time_ns)

    def _commit(self, devices: Iterable[Device], time_ns: int) -> dict[Signal, WriteState]:
        """One commit per device, and the states filled in with what the rig knows.

        The device reports the value after limits; the rig adds what was
        asked for, when the clamp changed it, and which controller drives
        the signal. The device's `written` gets the filled-in state too, so
        the wire shows one thing.
        """
        states: dict[Signal, WriteState] = {}
        for device in devices:
            for signal, state in device.commit(time_ns).items():
                holder = self.controllers.driving(signal)
                state = replace(
                    state,
                    requested=self._requested.pop(signal, None),
                    controller=None if holder is None else holder.name,
                )
                device.written[signal] = states[signal] = state
                if self.write_states.watched:
                    self.write_states.set(signal.address, state)
        return states

    def attach_controller(
        self,
        target: Signal,
        source: Signal,
        *,
        law: ControlLawLike | str | None = None,
        feedforward: FeedforwardLike | str | None = None,
        default: bool = False,
        min_period_s: float | None = None,
    ) -> Controller:
        """Regulate `source` through `target`; the controller is named by `target`'s address.

        Args:
            target: The W signal driven.
            source: The P signal regulated.
            law: The control law, a config, or a stored tuning's name.
            feedforward: What maps the setpoint to a demand in the target's
                unit: an instance, a config, or a tag. Default: the setpoint
                itself when the units agree, else none.
            default: Make this the controller commands address when they name none.
            min_period_s: Step the law at most this often.

        Raises:
            SourceClaimedError: `target` is already driven, or `source`
                already regulated, by another controller.
            ConflictError: `target` is not writable, `source` not publishing,
                or the feedforward cannot map the units.
        """
        if isinstance(law, str):
            law = self.tunings.get(law)
        if isinstance(feedforward, str):
            feedforward = Feedforwards[feedforward]()

        def write(demand: float) -> float | None:
            states = self.demand(target.node, {target: demand}, by=controller)
            return None if (state := states.get(target)) is None else state.value

        controller = Controller(
            self.clock,
            target,
            source,
            law=law,
            feedforward=feedforward,
            min_period_s=min_period_s,
            write=write,
        )
        self.controllers.add(controller, default=default)
        return controller

    def on_samples(self, samples: Sequence[NodeSample]) -> None:
        """One delivery: observers, then the controllers, one commit per touched device.

        A sample's keys must be bound, readable signals under its node, and
        there must be some: anything else is a driver bug, refused before
        any of the delivery is applied. Every reading lands in `latest`;
        only those on publishing signals go on to `samples` and the
        recorder, so a fresh read of a setting is known here without being
        streamed. Several controllers on one device, and a bound input
        beside them, cost that device one commit.

        Raises:
            ValueError: A sample carries a stray key, or none.
        """
        for sample in samples:
            self._check_sample(sample)
        if not samples:
            return
        ticks: list[tuple[Controller, SignalReading]] = []
        published: list[NodeSample] = []
        with self.lock:
            touched: dict[Device, None] = {}
            self._touched = touched
            try:
                for sample in samples:
                    self._last_samples[sample.node] = sample
                    if (streamed := sample.published()) is not None:
                        published.append(streamed)
                        if self.samples.watched:
                            self.samples.set(sample.node.address, streamed)
                    cuts: dict[Node, None] = {}
                    messages: dict[tuple[Device, Node], None] = {}
                    for reading in sample.readings():
                        signal = reading.signal
                        self.latest[signal] = reading
                        if (recent := self._recent_signal.get(signal)) is None:
                            recent = self._recent_signal[signal] = deque(maxlen=RECENT_READINGS)
                        recent.append(reading)
                        for device in self._observers.get(signal, ()):
                            device.observe(reading)
                            touched[device] = None
                        # Every node on the way up from the signal, not just
                        # the sample's, is an instant on that node: an atomic
                        # one keeps it, a device bound to it hears it.
                        node: Node | None = signal.node
                        while node is not None:
                            if node.atomic and node is not sample.node:
                                cuts[node] = None
                            for device in self._node_observers.get(node, ()):
                                messages[device, node] = None
                            node = node.parent
                        if (controller := self.controllers.find(signal)) is not None:
                            ticks.append((controller, reading))
                    for node in cuts:
                        if (cut := sample.under(node)) is not None:
                            self._last_cut[node] = cut
                    for device, node in messages:
                        if (message := sample.under(node)) is not None:
                            device.observe(message)
                            touched[device] = None
                for controller, reading in ticks:
                    controller.on_reading(reading)
                states = self._commit(touched, max(s.time_ns for s in samples))
            finally:
                self._touched = None
            for signal, state in states.items():
                if (controller := self.controllers.driving(signal)) is not None:
                    controller.delivered(state)
            if self.controller_states.watched:
                for controller, _ in ticks:
                    self.controller_states.set(controller.name, controller.state)
            # Step 4 (recorder): record `published`, `ticks` and `states` here.

    @staticmethod
    def _check_sample(sample: NodeSample) -> None:
        node = sample.node
        if not sample.values:
            raise ValueError(f"Sample on '{node.address}' carries no values")
        for key in sample.values:
            if not isinstance(key, Signal):
                what = f"'{key.address}' is a namespace" if isinstance(key, Node) else f"{key!r}"
                raise ValueError(f"Sample on '{node.address}' carries {what}, not a bound signal")
            if not node.contains(key):
                raise ValueError(
                    f"Sample on '{node.address}' carries '{key.address}', which is not under it"
                )
            if Access.R not in key.access:
                raise ValueError(
                    f"Sample on '{node.address}' carries '{key.address}' [{key.access}],"
                    " which is not readable"
                )

    def recent_signal_readings(
        self, signal: Signal, n: int = RECENT_READINGS
    ) -> list[SignalReading]:
        """The last `n` readings on `signal`, oldest first: a copy, so compute on it unlocked."""
        with self.lock:
            recent = self._recent_signal.get(signal)
            return [] if recent is None else list(recent)[-n:]

    def start_polling(self, device: Device) -> None:
        """Poll `device` on the smallest `poll_s` in its tree; nothing publishes on one: no-op."""
        if (period := poll_period(device)) is not None:
            self.polling.start(device, period)

    # endregion

    # legacy: goes in step 3
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
                if (recent := self._recent.get(channel)) is None:
                    recent = self._recent[channel] = deque(maxlen=RECENT_READINGS)
                recent.append(reading)  # one append on the delivery path; the maths is on request
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
