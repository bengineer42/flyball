"""The rig: every device by name, the controllers between their signals, and one delivery.

A device's samples arrive through [on_samples][flyball.runtime.rig.Rig.on_samples]
-- from a poll, a push, or a fresh read -- and one delivery runs observers,
then the controllers, then one `commit` per device touched, then the
recorder. Addresses are parsed once, at
[resolve][flyball.runtime.rig.Rig.resolve]; everything below carries the
bound objects.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import suppress
from threading import RLock
from typing import TYPE_CHECKING, Any, overload

from flyball.control import ControlLawLike, Controller, ControllerState, Tunings
from flyball.control.feedforward import FeedforwardLike, Feedforwards
from flyball.core import Clock
from flyball.core.device import (
    RESERVED_NAMES,
    Committable,
    Condition,
    Device,
    Event,
    Level,
    Readable,
)
from flyball.core.errors import ConflictError, NotFoundError, NotReadyError
from flyball.core.router import RECENT_READINGS, Router
from flyball.core.signal import (
    Access,
    AddressNotFoundError,
    Node,
    Reading,
    Sample,
    Signal,
    WriteState,
)
from flyball.core.topic import Latest, Topic
from flyball.core.typing import OrderedSet
from flyball.runtime.triggers import Triggers
from flyball.runtime.writer import Writer

from .controllers import Controllers
from .polling import Polling, poll_period

if TYPE_CHECKING:
    from flyball.db import Store

    from .recorder import Recorder

log = logging.getLogger("flyball.rig")


class Rig:
    clock: Clock
    devices: dict[str, Device]
    """Every device, by name: one namespace rig-wide.

    Links are not in it: they are declared in their own `links` section of
    a rig file and addressed through their own routes, so a link and a
    device may share a name.
    """
    name: str | None
    """What the rig file called it, if it came from one."""
    links: dict[str, Any]
    """What the rig file's `links` built, by name: buses, sessions, simulated plants."""
    lock: RLock
    triggers: Triggers
    """What is being waited on, by name: prompts, settle tests, holds."""
    events: Topic[Event]
    """Everything that happened, as it happens."""
    recent: deque[Event]
    """The last few hundred events, for a late joiner."""
    tunings: Tunings
    recorder: Recorder | None
    controllers: Controllers
    polling: Polling
    router: Router
    """Every current reading and latest sample; every device the rig holds shares it, and a
    push on it runs a delivery here."""
    samples: Latest[str, Sample]
    """The newest published sample per node address, for a watcher. Built only while watched."""
    write_states: Latest[str, WriteState]
    """The newest write state per signal address, after each commit. Likewise."""
    controller_states: Latest[str, ControllerState]
    """The newest state of each controller, by name, after each tick. Likewise."""
    _claims: dict[str, tuple[str, object]]
    """Every name claimed rig-wide, with who claimed it and as what, so a collision can be
    reported before it does anything harder to undo."""
    _writers: dict[Device, Writer]
    """A thread per blocking device, carrying its applies and commits to the bus."""
    _pushed: list[Sample]
    """Samples pushed from inside the delivery in progress (a commit's readbacks); delivered
    next, as one more delivery."""
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
        self.clock = Clock()
        self.devices = {}
        self._claims = {}
        self._writers = {}
        self.lock = RLock()
        self.triggers = Triggers(self.clock)
        self.events = Topic()
        self.recent = deque(maxlen=500)
        self.tunings = Tunings()
        self.recorder = None
        self.controllers = Controllers()
        self.polling = Polling(self)
        self.router = Router()
        self.router.deliver = self.on_samples
        self.router.now_ns = lambda: self.clock.now_ns()
        self.samples = Latest()
        self.write_states = Latest()
        self.controller_states = Latest()
        self._pushed = []
        self._observers = {}
        self._node_observers = {}
        self._requested = {}
        self._touched = None

    @property
    def latest(self) -> dict[Signal, Reading]:
        """The last reading delivered on each signal: the router's."""
        return self.router.latest

    # region Names

    def claim(self, name: str, kind: str, owner: object) -> None:
        """Reserve `name` for `kind` (a device, a simulation, ...) rig-wide.

        Re-claiming `name` for the same `owner` is a no-op. Claiming it for
        anything else is a collision, regardless of kind.

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
        """What claimed `name` -- "device", "simulation", ... -- or None."""
        claim = self._claims.get(name)
        return None if claim is None else claim[0]

    def add_device(self, device: Device) -> None:
        """Make a device reachable by name.

        Raises:
            ConflictError: The name is reserved, or already something else's.
        """
        self.claim(device.name, "device", device)
        self.devices[device.name] = device
        own, device.router = device.router, self.router
        if own.latest:  # what it pushed before it was added: initial values, configs
            now = self.clock.now_ns()
            by_node: dict[Node, dict[Signal, Any]] = {}
            for signal, reading in own.latest.items():
                by_node.setdefault(signal.node, {})[signal] = reading.value
            self.on_samples([Sample(node, now, values) for node, values in by_node.items()])

    # endregion

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

    def write_conditions(self) -> list[tuple[str, Condition]]:
        """Bus failures the writers of blocking devices are seeing now, by device name."""
        return [
            (device.name, writer.failed)
            for device, writer in self._writers.items()
            if writer.failed is not None
        ]

    def stop(self) -> None:
        """Stop what runs on threads: polling, writers, recording. The rig can be built again."""
        self.polling.stop_all()
        for writer in self._writers.values():
            writer.stop()
        self.stop_recording()

    # region Recording

    def start_recording(
        self,
        store: Store,
        signals: Iterable[Signal] | None = None,
        controllers: Iterable[Controller] | None = None,
        **session: Any,
    ) -> Recorder:
        """Open a session and record into it from the next delivery on.

        Defaults to every signal that publishes or is written, on every
        device, and every controller. Replaces a running recorder, closing
        its session first.
        """
        from .recorder import Recorder

        with self.lock:
            self.stop_recording()
            writer = store.open_session(self.clock.now_ns(), **session)
            if signals is None:
                signals = [
                    s
                    for device in self.devices.values()
                    for s in device.signals.values()
                    if Access.P in s.access or Access.W in s.access
                ]
            if controllers is None:
                controllers = [c for _, c in self.controllers.items()]
            self.recorder = Recorder(
                writer, signals, controllers, on_failure=self._recording_failed
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

    # region Addresses and reads

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
        if (device := self.devices.get(name)) is None:
            raise AddressNotFoundError(address, name, None)
        if dot and not relative:  # "hum." names nothing; "hum" is the root
            raise AddressNotFoundError(address, "", device.root.address)
        return device.root.find(relative)

    @overload
    def read(self, target: Signal, *, fresh: bool = False) -> Reading: ...
    @overload
    def read(self, target: Node, *, fresh: bool = False) -> Sample | Iterator[Sample]: ...
    @overload
    def read(
        self, target: Sequence[Node | Signal], *, fresh: bool = False
    ) -> list[Reading | Sample | Iterator[Sample]]: ...
    def read(
        self, target: Node | Signal | Sequence[Node | Signal], *, fresh: bool = False
    ) -> Reading | Sample | Iterator[Sample] | list[Reading | Sample | Iterator[Sample]]:
        """The last read on `target`: a Signal's Reading, an atomic Node's Sample, else Samples.

        `fresh` reads the hardware first and delivers what comes back as a
        poll would, so the rig's state and what the caller sees agree: how a
        setting (`RW`, never published) is read. An atomic node's sample is
        the newest instant on it, whether delivered on it or cut from a
        sample on another node of the tree. A sequence of targets gives a
        list of results in the order asked; a fresh read of several costs
        each device one `read`, on the node they share or its root.

        Raises:
            ConflictError: The signal is not readable.
            NotReadyError: Nothing has been read on it yet.
        """
        if isinstance(target, Sequence):
            with self.lock:
                if fresh:
                    self._read_fresh(target)
                return [self._read_known(t) for t in target]
        with self.lock:
            if fresh:
                self._read_fresh((target,))
            return self._read_known(target)

    def _read_fresh(self, targets: Iterable[Node | Signal]) -> None:
        """One `read` per device, on the one node asked for or the root, then one delivery."""
        nodes: dict[Device, Node | None] = {}
        for target in targets:
            node = target.node if isinstance(target, Signal) else target
            device = node.device
            if device not in nodes:
                nodes[device] = node
            elif nodes[device] is not node:
                nodes[device] = None  # the root
        time_ns = self.clock.now_ns()
        samples: list[Sample] = []
        for device, node in nodes.items():
            if not isinstance(device, Readable):
                raise ConflictError(f"'{device.name}' has nothing to read")
            samples.extend(device.read(time_ns, node))
        self.on_samples(samples)

    def _read_known(self, target: Node | Signal) -> Reading | Sample | Iterator[Sample]:
        if isinstance(target, Signal):
            if Access.R not in target.access:
                raise ConflictError(f"'{target.address}' [{target.access}] is not readable")
            if (reading := self.router.reading(target)) is None:
                raise NotReadyError(f"Nothing has been read on '{target.address}' yet")
            return reading
        if target.atomic:
            if (sample := self.router.sample(target)) is None:
                raise NotReadyError(f"Nothing has been read on '{target.address}' yet")
            return sample
        return iter(list(self.router.samples_under(target)))

    def recent_readings(self, signal: Signal, n: int = RECENT_READINGS) -> list[Reading]:
        """The last `n` readings on `signal`, oldest first: a copy, so compute on it unlocked."""
        with self.lock:
            return self.router.recent_readings(signal, n)

    def start_polling(self, device: Device) -> None:
        """Poll `device` on the smallest `poll_s` in its tree; nothing publishes on one: no-op."""
        if device.readable and (period := poll_period(device)) is not None:
            self.polling.start(device, period)

    # endregion

    # region Writes

    def demand(
        self, node: Node, values: Mapping[str | Signal, float], *, by: Controller | None = None
    ) -> Mapping[Signal, WriteState]:
        """Put `values` on W signals under `node`, as one demand, in each signal's unit.

        A key is a bound signal under `node`, or its name relative to
        `node`, dotted for a namespace -- the wire's form, resolved here and
        nowhere below. The whole demand is checked before anything is
        recorded -- every key a W signal, no signal another controller's --
        then clamped to `limits` and fanned out to `device.apply`. A demand
        from outside a delivery is committed now and its states returned;
        one from inside a delivery (a controller's, a command's) is
        committed with everything else at its end, and this returns nothing.
        A blocking device's commit runs on its writer thread, so its states
        arrive later, through [written][flyball.runtime.rig.Rig.written];
        this returns nothing.

        Raises:
            AddressNotFoundError: A name does not resolve under `node`.
            ConflictError: A key is not a writable signal under `node`, or
                is driven by a controller.
            ValueError: No values, or one signal named twice.
        """
        if not values:
            raise ValueError(f"Demand on '{node.address}' carries no values")
        device = node.device
        if not isinstance(device, Committable):
            raise ConflictError(f"'{device.name}' has nothing to commit: no demands")
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
        with self.lock:
            time_ns = self.clock.now_ns()
            writer = self._writer_for(device)
            for signal, value in clamped.items():
                if writer is None:
                    device.apply(signal, time_ns, value)
                else:
                    writer.apply(signal, time_ns, value)
            for signal in clamped:  # a newer demand supersedes an earlier clamped one
                if signal in requested:
                    self._requested[signal] = requested[signal]
                else:
                    self._requested.pop(signal, None)
            if self._touched is not None:  # inside a delivery: committed at its end
                self._touched[device] = None
                return {}
            states = self._committing((device,), time_ns)
            if states and self.recorder is not None:
                self.recorder.record((), (), states, time_ns=time_ns)
            self._flush_pushed()
            return states

    def _writer_for(self, device: Device) -> Writer | None:
        """The thread in front of a device that blocks on a bus; None for one that does not."""
        if not device.blocking or not isinstance(device, Committable):
            return None
        if (writer := self._writers.get(device)) is None:
            writer = self._writers[device] = Writer(self, device)
        return writer

    def _committing(self, devices: Iterable[Device], time_ns: int) -> dict[Signal, WriteState]:
        """Commit outside a delivery: the readbacks the commits push are queued, then delivered."""
        self._touched = {}
        try:
            return self._commit(devices, time_ns)
        finally:
            self._touched = None

    def _commit(self, devices: Iterable[Device], time_ns: int) -> dict[Signal, WriteState]:
        """One commit per device, and the states filled in with what the rig knows.

        A blocking device's commit is handed to its writer instead, and its
        states come back through `written` when the write completes.
        """
        states: dict[Signal, WriteState] = {}
        for device in devices:
            if not isinstance(device, Committable):
                continue  # touched by an input landing; nothing to commit
            if (writer := self._writer_for(device)) is not None:
                writer.request(time_ns)
            else:
                before = dict(self.router.seq)
                device.commit(time_ns)
                states.update(self._states(device, time_ns, before))
        return states

    def _states(
        self, device: Committable, time_ns: int, before: Mapping[Signal, int]
    ) -> dict[Signal, WriteState]:
        """What a commit set each pending demand to, with what the rig knows; clears `pending`.

        The driver may have pushed a readback in `commit` (`before` is the
        router's count per signal from before it ran); a demand it did not
        push gets the committed value as its reading, so every demand has a
        current value. The rig adds what was asked for, when the clamp
        changed it, and which controller drives the signal; the device's
        `written` gets the filled-in state too, so the wire shows one thing.
        """
        states: dict[Signal, WriteState] = {}
        seq = self.router.seq
        for signal, value in device.pending.items():
            at_limit = signal.at_limit
            if at_limit is None and (limits := signal.limits) is not None:
                if value <= limits[0]:
                    at_limit = "low"
                elif value >= limits[1]:
                    at_limit = "high"
            holder = self.controllers.driving(signal)
            state = WriteState(
                value=value,
                requested=self._requested.pop(signal, None),
                at_limit=at_limit,
                controller=None if holder is None else holder.name,
            )
            device.written[signal] = states[signal] = state
            if self.write_states.watched:
                self.write_states.set(signal.address, state)
            signal.at_limit = None
            if seq.get(signal, 0) == before.get(signal, 0):  # the driver pushed no readback
                signal.push(value, time_ns)
        device.pending.clear()
        return states

    def _flush_pushed(self) -> None:
        """Deliver what commits pushed, each batch as one more delivery, until nothing is left."""
        while self._pushed:
            pushed, self._pushed = self._pushed, []
            self._deliver_samples(pushed, noted=True)

    def written(self, device: Committable, time_ns: int) -> None:
        """A blocking device's writer finished a commit: publish, deliver and record its states."""
        with self.lock:
            self._touched = {}  # the context for the readbacks `_states` pushes
            try:
                filled = self._states(device, time_ns, {})
            finally:
                self._touched = None
            self._deliver(filled)
            if filled and self.recorder is not None:
                self.recorder.record((), (), filled, time_ns=time_ns)
            self._flush_pushed()

    def _deliver(self, states: Mapping[Signal, WriteState]) -> None:
        """Close each controller's tick with the state its target was committed to."""
        for signal, state in states.items():
            if (controller := self.controllers.driving(signal)) is not None:
                controller.delivered(state)
                if self.controller_states.watched:
                    self.controller_states.set(controller.name, controller.state)

    # endregion

    # region Commands

    def run_command(self, device: Device, tag: str, args: Mapping[str, Any] | None = None) -> Any:
        """Run `device`'s command `tag` with `args`, as the rig: linked, clamped, owned, recorded.

        An argument that is a value for a demand (`For[...]`) is filled from
        that demand's current value when left out, and clamped to the
        signal's effective limits. A synthesised `set_<name>` goes through
        [demand][flyball.runtime.rig.Rig.demand]. A command that changes
        what drives the device -- one with a `mode`, or a linked argument --
        is refused while a controller drives one of the device's demands,
        unless it is `owner_exempt`. The method runs under the rig lock; afterwards the
        device's `mode` output (if it has one) becomes the command's, a
        `commit=True` command commits the device, each linked demand the
        driver did not push gets its argument as its reading, and
        `last.<tag>` records what ran. Returns what the method returned.

        Raises:
            NotFoundError: No such command.
            ConflictError: A controller drives the device.
            NotReadyError: A linked argument was left out and its demand has
                no value yet.
        """
        try:
            spec = type(device).commands[tag]
        except KeyError as e:
            raise NotFoundError(f"{device.name!r} has no command {tag!r}") from e
        given = dict(args or {})
        if spec.demand_of is not None:
            signal = device.signals[spec.demand_of]
            return self.demand(signal.node, {signal: given["value"]})
        with self.lock:
            linked: dict[str, Signal] = {}
            for name, param in spec.params.items():
                if param.link is None:
                    continue
                signal = linked[name] = device.signals[param.link]
                if name not in given:
                    given[name] = self.router.value(signal)
                elif (limits := signal.limits) is not None and isinstance(
                    given[name], (int, float)
                ):
                    given[name] = min(max(float(given[name]), limits[0]), limits[1])
            if not spec.owner_exempt and (spec.mode is not None or linked):
                # It changes what drives the device: not while a controller does.
                for signal in device.signals.values():
                    holder = self.controllers.driving(signal)
                    if holder is not None and holder.mode.active():
                        raise ConflictError(
                            f"'{signal.address}' is driven by controller {holder.name!r}:"
                            f" {tag!r} would fight it; put it in manual, or detach it"
                        )
            time_ns = self.clock.now_ns()
            outer = self._touched
            if outer is None:
                self._touched = {}
            before = dict(self.router.seq)
            try:
                result = spec.method(device, **given)
                if spec.mode is not None and (mode := device.signals.get("mode")) is not None:
                    mode.push(spec.mode, time_ns)
                for name, signal in linked.items():
                    if self.router.seq.get(signal, 0) == before.get(signal, 0):  # no readback
                        signal.push(given[name], time_ns)
                if (last := device.signals.get(f"last.{tag}")) is not None:
                    last.push({"args": given, "at": time_ns}, time_ns)
                if spec.commit:
                    if outer is not None:
                        outer[device] = None
                    else:
                        states = self._commit((device,), time_ns)
                        if states and self.recorder is not None:
                            self.recorder.record((), (), states, time_ns=time_ns)
            finally:
                if outer is None:
                    self._touched = None
            if outer is None:
                self._flush_pushed()
            return result

    # endregion

    # region Controllers

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

    def detach_controller(self, name: str) -> Controller:
        """Take the controller off its target: manual demands may drive it again.

        The controller is left in manual with nothing to write to.

        Raises:
            ControllerNotFoundError: No controller of that name.
        """
        with self.lock:
            controller = self.controllers.remove(name)
            controller.manual()
            controller.write = Controller._unwired
            # A watcher primes from this cell; a name the rig no longer has must not be in it.
            self.controller_states.discard(name)
            return controller

    # endregion

    # region Delivery

    def on_samples(self, samples: Sequence[Sample]) -> None:
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
        with self.lock:
            if self._touched is not None:
                # Pushed from inside a delivery (a commit's readbacks, a
                # mode): known at once, so the driver reads what it just
                # pushed; delivered next, once this delivery has committed.
                for sample in samples:
                    self.router.note(sample)
                self._pushed.extend(samples)
                return
            self._deliver_samples(samples)
            self._flush_pushed()

    def _deliver_samples(self, samples: Sequence[Sample], *, noted: bool = False) -> None:
        """One delivery, under the lock: note, observers, controllers, commits, recorder."""
        ticks: list[tuple[Controller, Reading]] = []
        published: list[Sample] = []
        touched: dict[Device, None] = {}
        self._touched = touched
        try:
            for sample in samples:
                if not noted:
                    self.router.note(sample)
                if (streamed := sample.published()) is not None:
                    published.append(streamed)
                    if self.samples.watched:
                        self.samples.set(sample.node.address, streamed)
                messages: dict[tuple[Device, Node], None] = {}
                for reading in sample.readings():
                    signal = reading.signal
                    for device in self._observers.get(signal, ()):
                        touched[device] = None  # it reads the router in `commit`
                    # Every node on the way up from the signal, not just
                    # the sample's, is an instant on that node: a device
                    # bound to it hears it.
                    node: Node | None = signal.node
                    while node is not None:
                        for device in self._node_observers.get(node, ()):
                            messages[device, node] = None
                        node = node.parent
                    if (controller := self.controllers.find(signal)) is not None:
                        ticks.append((controller, reading))
                for device, node in messages:
                    # A subscriber hears what publishes, as a signal-level
                    # binding requires P; a fresh read of an R-only setting
                    # is for whoever asked for it.
                    if (message := sample.under(node)) is not None and (
                        message.published()
                    ) is not None:
                        touched[device] = None
            for controller, reading in ticks:
                controller.on_reading(reading)
            time_ns = max(s.time_ns for s in samples)
            states = self._commit(touched, time_ns)
        finally:
            self._touched = None
        self._deliver(states)
        if self.controller_states.watched:
            for controller, _ in ticks:
                self.controller_states.set(controller.name, controller.state)
        if self.recorder is not None:
            self.recorder.record(published, ticks, states, time_ns=time_ns)

    @staticmethod
    def _check_sample(sample: Sample) -> None:
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

    # endregion
