"""The rig: every device by name, the controllers between their signals, and one delivery.

A device's samples arrive through [on_samples][flyball.rig.rig.Rig.on_samples]
-- from a poll, a push, or a fresh read -- and one delivery runs observers,
then the controllers, then one `commit` per device touched, then the
recorder. Addresses are parsed once, at
[resolve][flyball.rig.rig.Rig.resolve]; everything below carries the
bound objects.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, overload

from flyball.foundation import Clock, Rate
from flyball.foundation.device import (
    RESERVED_NAMES,
    Access,
    AddressNotFoundError,
    Code,
    CommandSpec,
    Committable,
    Conditions,
    Device,
    DeviceEntry,
    Event,
    Limit,
    LimitNotKnownError,
    LimitsInvertedError,
    Node,
    NoValue,
    Readable,
    Readback,
    Reading,
    Reason,
    Role,
    Sample,
    Scope,
    Severity,
    Signal,
    Staged,
    WriteState,
    normalised,
    stale,
)
from flyball.foundation.errors import ConflictError, NotFoundError, NotReadyError
from flyball.foundation.router import RECENT_READINGS, Latest, Router, Topic
from flyball.foundation.typing import OrderedSet
from flyball.library.tunings import Tunings
from flyball.model.catalog import get_catalog
from flyball.model.controller import Controller, ControllerState
from flyball.model.feedforward import FeedforwardLike
from flyball.model.law import ControlLawLike
from flyball.runtime.writer import Writer

from .bands import Bands
from .controllers import Controllers
from .polling import Polling, poll_period
from .triggers import Triggers

if TYPE_CHECKING:
    from flyball.record import Store
    from flyball.runtime.recorder import Recorder

log = logging.getLogger("flyball.rig")

RESUME_AFTER = 3
"""Readings with a value, in a row, before a controller frozen on a no-value steps again."""

FRESH_READ_WAIT_S = 5.0
"""How long a fresh read waits for a read of the same device already in flight (a poll,
another fresh read) before it is refused."""


def _held(lock: RLock) -> bool:
    """Whether the calling thread holds `lock` (an `RLock`, or a test's wrapper of one)."""
    return lock._is_owned()  # type: ignore[attr-defined]  # CPython's RLock has no public form


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
    conditions: Conditions
    """What is true now of each device, signal, controller and the rig itself, keyed by the
    object: offline, slow, a failing write, a held controller. Each start and end is an event
    (`raised`, `cleared`); in-process subscribers hear them too."""
    bands: Bands
    """Each banded signal's `band_warning` / `band_alarm`, kept from its readings on delivery."""
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
    _stepped: set[Controller] | None
    """The controllers stepped since the outermost delivery began, through every delivery of
    what its commits pushed; None outside one. A controller steps at most once in that chain:
    a commit that pushes back its own measured signal would otherwise step it again, for ever."""
    _running: dict[Device, str]
    _write_lost: dict[Device, set[Signal]]
    """Per device, the demands whose last commit failed and has not been made good since: they
    stay `stale(write_failed)` after the device's writes recover, until a demand of theirs
    commits."""
    _read_path: dict[Device, set[Signal]]
    """Per device, the signals its polled reads have delivered: what goes
    `stale(device_offline)` when it goes offline."""
    _fresh: dict[Controller, int]
    """Per controller, how many readings in a row with a value its measured signal has had."""
    """The long command each device is running now, off the lock (`@command(long=True)`)."""
    _ignored: set[Signal]
    """Demands whose driver's `commit` did not read them: one event when a signal's demand
    first goes unread, none per demand after, until one is read again."""
    entries: dict[str, DeviceEntry]
    """What each device was built from: its rig-file entry, for rendering the rig back out."""
    link_entries: dict[str, Any]
    """What each link was built from: its config, likewise."""
    files: list[Path]
    """The rig files the runner loaded, if any; provenance for a version."""
    header: dict[str, Any]
    """The loaded document's keys that are not links, devices or controllers (`board`, `clock`,
    `recording`), carried into the rendered document unchanged."""
    loaded: dict[str, Any] | None
    """The rig as it was when this run started, rendered: what `changes` are measured from."""
    saved_overlay: dict[str, Any]
    """The runner's own saved overlay (`<rig>.d/added.*`) as this run loaded it; empty if there
    was none. `loaded` already includes it, so a save writes it merged with this run's changes."""
    on_change: Callable[[str], None] | None
    """Called after the rig's composition changes (a link, a device, a controller added or
    removed), with a one-line reason: the runner records a version."""
    on_recording_stopped: Callable[[], None] | None
    """Called after `stop_recording` closes a session, outside the lock: the runner reopens
    its scratch record. Not called when a recording replaces another, nor by `close`."""
    _closed: bool

    def __init__(self, name: str | None = None) -> None:
        self.name = name
        self.links = {}
        self.clock = Clock()
        self.devices = {}
        self._claims = {}
        self._writers = {}
        self.lock = RLock()
        self.triggers = Triggers(lambda: self.clock)
        self.events = Topic()
        self.recent = deque(maxlen=500)
        self.conditions = Conditions(
            now_ns=lambda: self.clock.now_ns(), describe=self._describe, emit=self._publish
        )
        self.bands = Bands(self.conditions, lambda: self.clock.now_ns())
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
        self._stepped = None
        self._ignored = set()
        self._running = {}
        self._write_lost = {}
        self._read_path = {}
        self._fresh = {}
        self.entries = {}
        self.link_entries = {}
        self.files = []
        self.header = {}
        self.loaded = None
        self.saved_overlay = {}
        self.on_change = None
        self.on_recording_stopped = None
        self._closed = False

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
            ValueError: A signal's limit follows something that is neither a
                signal nor an input of the device.
        """
        for signal in device.signals.values():
            signal.bind_limits()  # a limit naming nothing fails here, not at the first demand
        self.claim(device.name, "device", device)
        self.devices[device.name] = device
        device.clock_source = lambda: self.clock  # its long commands wait on the rig's time
        # What its driver raised before it was added is raised here, on the rig's clock.
        held, device.conditions = device.conditions.items(), self.conditions
        for owner, condition in held:
            self.conditions.set(
                owner, condition.code, condition.severity, condition.message, condition.details
            )
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
        severity: Severity,
        scope: Scope,
        subject: str,
        code: Code,
        message: str,
        details: Any = None,
    ) -> Event:
        """Record that something happened: logged, kept, pushed to watchers, recorded.

        A point event. What starts and ends -- a condition -- goes through
        [conditions][flyball.rig.rig.Rig.conditions], whose edges come here too.
        """
        event = Event(self.clock.now_ns(), severity, scope, subject, code, message, details)
        self._publish(event)
        return event

    def _publish(self, event: Event) -> None:
        """Log, keep, stream and record one event.

        A condition's edge on a device, or on one of its signals (a band),
        also refreshes the device's run, so a watcher of `runs` sees its
        conditions change.
        """
        edge = f" {event.edge}" if event.edge is not None else ""
        log.log(
            Severity(event.severity).rank,
            "%s %s %s%s: %s",
            event.scope,
            event.subject,
            event.code,
            edge,
            event.message,
        )
        self.recent.append(event)
        self.events.publish(event)
        if (recorder := self.recorder) is not None:
            recorder.event(event)
        if event.edge is not None and event.scope == Scope.DEVICE:
            self.polling.touch(event.subject)
        elif event.edge is not None and event.scope == Scope.SIGNAL:
            self.polling.touch(event.subject.partition(".")[0])  # its device's run carries it

    def _describe(self, owner: object) -> tuple[str, str]:
        """A condition owner's scope and name: a device, a signal, a controller, or this rig."""
        if owner is self:
            return Scope.RIG, self.name or "rig"
        if isinstance(owner, Device):
            return Scope.DEVICE, owner.name
        if isinstance(owner, Signal):
            return Scope.SIGNAL, owner.address
        if isinstance(owner, Controller):
            return Scope.CONTROLLER, owner.name
        raise TypeError(f"{type(owner).__name__} cannot own a condition")

    def close(self) -> None:
        """Tear down: polling, writers, recording, links. The rig can be built again.

        Idempotent; a second call is a no-op.
        """
        if self._closed:
            return
        self._closed = True
        with self.lock:  # copies: a delivery or a request may add to them meanwhile
            running = list(self._running)
            writers = list(self._writers.values())
            links = list(self.links.items())
        for device in running:
            device.cancel()
        self.polling.stop_all()
        for writer in writers:  # joined outside the lock: a write in flight reports under it
            writer.stop()
        self._stop_recording()
        self.conditions.close()
        for name, link in links:
            close = getattr(link, "close", None)
            if close is None:
                continue
            try:
                close()
            except Exception:
                log.exception("link %r: close failed", name)

    # region Recording

    @property
    def recording(self) -> Recorder | None:
        """The recorder of a session someone started; None under scratch alone, or nothing."""
        recorder = self.recorder
        return None if recorder is None or recorder.writer.session.scratch else recorder

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
        its session first. `session` is what the store's `open_session`
        takes; a `start_ns` in it backdates the session (for what is then
        backfilled), else it starts now.
        """
        from flyball.runtime.recorder import Recorder

        with self.lock:
            self._stop_recording()
            start_ns = session.pop("start_ns", None)
            writer = store.open_session(
                self.clock.now_ns() if start_ns is None else start_ns, **session
            )
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
            self.conditions.clear(self, Code.RECORDING_FAILED, message="recording again")
            return self.recorder

    def _recording_failed(self, error: Exception) -> None:
        """From the recorder's thread: detach it first, so the edge does not go back to it.

        A `recording_failed` condition on the rig until the next recording starts.
        """
        with self.lock:
            recorder, self.recorder = self.recorder, None
        self.conditions.set(
            self,
            Code.RECORDING_FAILED,
            Severity.ERROR,
            f"recording stopped: {type(error).__name__}: {error}",
        )
        if recorder is not None:
            with suppress(Exception):  # the store already failed once
                recorder.writer.end(self.clock.now_ns())

    def stop_recording(self) -> None:
        """Close the open session, if any, and tell `on_recording_stopped`."""
        if self._stop_recording() and self.on_recording_stopped is not None:
            self.on_recording_stopped()

    def _stop_recording(self) -> bool:
        """Close the open session; whether there was one."""
        with self.lock:
            if (recorder := self.recorder) is None:
                return False
            self.recorder = None
            recorder.close(self.clock.now_ns())
            return True

    # endregion

    # region Addresses and reads

    def bind_inputs(self, device: Device, inputs: Mapping[str, str]) -> None:
        """Resolve `device`'s inputs (the rig file's `inputs:`, name -> address) and follow them.

        Call after every device is added: an address may name a device
        declared later in the file. From then on, every reading on a bound
        signal reaches `device.observe` in the delivery it arrives in; for a
        bound node, every sample carrying something under it does, cut down
        to that, after the readings.

        Raises:
            AddressNotFoundError: An address does not resolve; names it.
            ConflictError: A signal that does not publish, or a node with
                nothing published under it.
        """
        for role, address in inputs.items():
            target = self.resolve(address)
            if isinstance(target, Signal):
                if Access.P not in target.access:
                    raise ConflictError(
                        f"{device.name}.inputs.{role}: '{target.address}' [{target.access}]"
                        " is not published"
                    )
                self._observers.setdefault(target, {})[device] = None
            else:
                if not any(Access.P in s.access for s in target.walk()):
                    raise ConflictError(
                        f"{device.name}.inputs.{role}: nothing under '{target.address}' publishes"
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
        setting (`RW`, never published) is read. The read itself runs off the
        rig lock, under the device's `read_lock` (one read of a device at a
        time), and only its delivery takes the rig lock. An atomic node's sample is
        the newest instant on it, whether delivered on it or cut from a
        sample on another node of the tree. A sequence of targets gives a
        list of results in the order asked; a fresh read of several costs
        each device one `read`, on the node they share or its root.

        Raises:
            ConflictError: The signal is not readable; a fresh read of a
                device with nothing to read; a fresh read while another read
                of the device has been in flight for `FRESH_READ_WAIT_S`; or
                a fresh read asked for under the rig lock.
            NotReadyError: Nothing has been read on it yet.
        """
        if isinstance(target, Sequence):
            if fresh:
                self._read_fresh(target)
            with self.lock:
                return [self._read_known(t) for t in target]
        if fresh:
            self._read_fresh((target,))
        with self.lock:
            return self._read_known(target)

    def _read_fresh(self, targets: Iterable[Node | Signal]) -> None:
        """One `read` per device, on the one node asked for or the root, then one delivery.

        Each read under its device's `read_lock`, off the rig lock; the
        delivery under it. What a device removed meanwhile read is dropped.
        """
        if _held(self.lock):
            raise ConflictError("a fresh read cannot run while the rig lock is held")
        nodes: dict[Device, Node | None] = {}
        for target in targets:
            node = target.node if isinstance(target, Signal) else target
            device = node.device
            if device not in nodes:
                nodes[device] = node
            elif nodes[device] is not node:
                nodes[device] = None  # the root
        for device in nodes:
            if not isinstance(device, Readable):
                raise ConflictError(f"'{device.name}' has nothing to read")
        read: list[tuple[Device, list[Sample]]] = []
        for device, node in nodes.items():
            assert isinstance(device, Readable)
            if not device.read_lock.acquire(timeout=FRESH_READ_WAIT_S):
                raise ConflictError(
                    f"'{device.name}': a read has been in flight for over"
                    f" {FRESH_READ_WAIT_S:g} s; try again when it returns"
                )
            try:
                read.append((device, list(device.read(self.clock.now_ns(), node))))
            finally:
                device.read_lock.release()
        with self.lock:
            live = [s for d, samples in read if self.devices.get(d.name) is d for s in samples]
            self.on_samples(live)

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

    def write(
        self, node: Node, values: Mapping[str | Signal, float], *, by: Controller | None = None
    ) -> Mapping[Signal, WriteState]:
        """Write `values` to W signals under `node`, as one atomic write, in each signal's unit.

        A key is a bound signal under `node`, or its name relative to
        `node`, dotted for a namespace -- the wire's form, resolved here and
        nowhere below. The whole demand is checked before anything is
        recorded -- every key a W signal, none driven by an active
        controller, every limit known -- then clamped to `limits` and
        fanned out to `device.apply`. A limit that follows a signal with no
        value yet fails closed: a manual demand is refused whole. A
        controller's demand is held (nothing applied, `{}` returned) for
        any reason [hold_reason][flyball.rig.rig.Rig.hold_reason] gives --
        that limit, or a stale measured signal -- which the controller has already
        asked before stepping its law. A demand
        from outside a delivery is committed now and its states returned;
        one from inside a delivery (a controller's, a command's) is
        committed with everything else at its end, and this returns nothing.
        A blocking device's commit runs on its writer thread, so its states
        arrive later, through [written][flyball.rig.rig.Rig.written];
        this returns nothing.

        Raises:
            AddressNotFoundError: A name does not resolve under `node`.
            ConflictError: A key is not a writable signal under `node`, or
                is driven by a controller.
            LimitNotKnownError: A signal's limit follows a signal with no
                value yet (a `NotReadyError`); not raised for a controller's
                demand, which is held instead.
            LimitsInvertedError: A signal's limits resolve inverted now, low
                above high (an `UnachievableError`); held for a controller's
                demand, like an unknown limit.
            ValueError: No values, one signal named twice, or a value that is
                not finite (NaN, infinity).
        """
        if not values:
            raise ValueError(f"Demand on '{node.address}' carries no values")
        device = node.device
        if not isinstance(device, Committable):
            raise ConflictError(f"'{device.name}' has nothing to commit: no demands")
        if by is not None and self.hold_reason(by) is not None:
            return {}
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
            if not math.isfinite(resolved[signal]):
                # NaN slips through every comparison: the limits, the rate clamp.
                raise ValueError(f"Demand on '{signal.address}' is not finite: {value!r}")
        clamped: dict[Signal, float] = {}
        requested: dict[Signal, float] = {}
        now_ns = self.clock.now_ns()
        for signal, value in resolved.items():
            if Access.W not in signal.access:
                raise ConflictError(f"'{signal.address}' [{signal.access}] is not writable")
            holder = self.controllers.driving(signal)
            if holder is not None and holder is not by and holder.mode.active():
                # As a command: refused while the controller drives it; in
                # manual the output takes demands directly.
                raise ConflictError(
                    f"'{signal.address}' is driven by controller {holder.name!r}:"
                    " set its reference, put it in manual, or detach it"
                )
            original = value
            try:
                value = signal.clamp(value)
            except (LimitNotKnownError, LimitsInvertedError) as e:
                if by is None:
                    raise
                self._limit_unknown(by, e)
                return {}
            if (max_rate := signal.spec.max_rate) is not None:
                value = self._rate_clamped(signal, value, max_rate, now_ns, by)
            if value != original:
                requested[signal] = original
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
            try:
                states = self._committing((device,), time_ns)
                if states and self.recorder is not None:
                    self.recorder.record((), (), states, time_ns=time_ns)
            finally:  # a failed commit's stale demands are delivered too
                self._flush_pushed()
            return states

    def hold_reason(self, controller: Controller) -> Code | None:
        """Why a write by `controller` would be held now, or None if it would go through.

        `stale_input`: its measured signal has not been read within `stale_after_s`.
        `limit_unknown`: a limit on its output follows a signal with no value
        yet, or a non-finite one (D-030), or the limits resolve inverted
        (D-040). Each is a condition on the controller while it lasts: raised
        on entering the hold, cleared on leaving it, nothing per call between.
        The controller asks this before it steps its law, so a held write
        freezes the law as well as the output; `demand` asks it again for its
        own writes.

        `frozen`: its measured signal's newest reading has no value (`invalid`,
        `stale`, `not_applicable`); nothing is substituted. It stays frozen
        until `RESUME_AFTER` readings in a row have a value, then steps on,
        its law's clock skipping the freeze.
        """
        measured = controller.measured_signal
        latest = self.router.latest.get(measured)
        if latest is not None and isinstance(value := latest.value, NoValue):
            why = f": {value.reason}" if value.reason else ""
            self.conditions.set(
                controller,
                Code.FROZEN,
                Severity.WARNING if value.quality.fault else Severity.INFO,
                f"'{measured.address}' has no value ({value.quality.value}{why}): frozen",
                {
                    "signal": measured.address,
                    "quality": value.quality.value,
                    "reason": value.reason,
                },
            )
            return Code.FROZEN
        if self.conditions.get(controller, Code.FROZEN) is not None:
            if (fresh := self._fresh.get(controller, 0)) < RESUME_AFTER:
                return Code.FROZEN
            self.conditions.clear(
                controller, Code.FROZEN, message=f"{fresh} readings with a value: stepping again"
            )
        if (stale_after_s := controller.measured_signal.spec.stale_after_s) is not None:
            measured = controller.measured_signal
            reading = self.router.latest.get(measured)
            age_s = None if reading is None else (self.clock.now_ns() - reading.time_ns) / 1e9
            if age_s is None or age_s > stale_after_s:
                self.conditions.set(
                    controller,
                    Code.STALE_INPUT,
                    Severity.WARNING,
                    f"'{measured.address}' has not been read in over {stale_after_s:g}s: held",
                    {"age_s": age_s},
                )
                return Code.STALE_INPUT
            self.conditions.clear(controller, Code.STALE_INPUT, message="read again: writing")
        try:
            controller.output_signal.clamp(0.0)
        except (LimitNotKnownError, LimitsInvertedError) as e:
            self._limit_unknown(controller, e)
            return Code.LIMIT_UNKNOWN
        self.conditions.clear(
            controller,
            Code.LIMIT_UNKNOWN,
            message="every limit on its output is known: writing again",
        )
        return None

    def _limit_unknown(
        self, controller: Controller, error: LimitNotKnownError | LimitsInvertedError
    ) -> None:
        """Hold `limit_unknown` on the controller: raised once, not per step."""
        self.conditions.set(
            controller,
            Code.LIMIT_UNKNOWN,
            Severity.WARNING,
            f"{error}: held",
            {"signal": error.address, "unknown": error.unknown},
        )

    def _rate_clamped(
        self,
        signal: Signal,
        value: float,
        max_rate: Rate,
        now_ns: int,
        by: Controller | None = None,
    ) -> float:
        """`value`, held to at most `max_rate` away from the last commit's, over the elapsed time.

        The elapsed time counts up to one update period and no further: the
        signal's `poll_s`, else the demanding controller's `min_period_s`,
        else 1 s. A demand after a long quiet spell (a hold, an idle signal)
        moves one period's worth, not the whole allowance banked meanwhile.

        Nothing to compare against yet (no prior commit), or a last value
        that is not finite (a readback gone wrong): `value` passes through
        unclamped, as the first demand on a signal has nothing to ramp from.
        A demand whose newest reading has no value (`stale(write_failed)`)
        ramps from the last one that had.
        """
        last = self.router.last_usable.get(signal)
        if last is None or not isinstance(last.value, int | float) or not math.isfinite(last.value):
            return value
        window_s = signal.poll_s or (by.min_period_s if by is not None else None) or 1.0
        elapsed_s = min((now_ns - last.time_ns) / 1e9, window_s)
        if elapsed_s <= 0:
            return last.value
        step = max_rate.per_second * elapsed_s
        return min(max(value, last.value - step), last.value + step)

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

    def _commit(
        self,
        devices: Iterable[Device],
        time_ns: int,
        failed: dict[Signal, WriteState] | None = None,
    ) -> dict[Signal, WriteState]:
        """One commit per device, and the states filled in with what the rig knows.

        A blocking device's commit is handed to its writer instead, and its
        states come back through `written` when the write completes.

        A commit that raises is that device's alone: its demands are dropped
        (not left in `staged` to go out with a later commit, over a newer
        write), it becomes a `commit_failed` event once per outage and a
        condition, and the other devices commit regardless. Into `failed`,
        when given, go the states of what it dropped (`value` None: nothing
        was set), for the controllers driving them; without it -- a manual
        demand or a command, one device -- the error is raised to the caller
        too.
        """
        states: dict[Signal, WriteState] = {}
        for device in devices:
            if not isinstance(device, Committable):
                continue  # touched by an input landing; nothing to commit
            if (writer := self._writer_for(device)) is not None:
                writer.request(time_ns)
                continue
            before = dict(self.router.seq)
            try:
                device.commit(time_ns)
            except Exception as error:
                dropped = self._commit_failed(device, error)
                if failed is None:
                    raise
                failed.update(dropped)
                continue
            recovered = self.conditions.clear(device, Code.COMMIT_FAILED, message="commits succeed")
            states.update(self._states(device, time_ns, before))
            if recovered is not None:
                self._writes_recovered(device)
        return states

    def _commit_failed(self, device: Committable, error: Exception) -> dict[Signal, WriteState]:
        """A commit raised: drop its demands, report the outage once; what each demand became."""
        message = f"{type(error).__name__}: {error}"
        raised = self.conditions.set(  # raised once per outage, not once per delivery
            device,
            Code.COMMIT_FAILED,
            Severity.ERROR,
            message,
            {"signals": [signal.address for signal in device.staged]},
        )
        if raised:
            log.warning("%s: commit failed: %s", device.name, message, exc_info=error)
        dropped: dict[Signal, WriteState] = {}
        for signal in device.staged:
            holder = self.controllers.driving(signal)
            dropped[signal] = WriteState(
                value=None,
                requested=self._requested.pop(signal, None),
                controller=None if holder is None else holder.name,
            )
            signal.at_limit = None
        self._writes_failing(device, list(dict.keys(device.staged)))
        device.staged.clear()
        return dropped

    def writes_failed(self, device: Device, signals: Iterable[Signal]) -> None:
        """A blocking device's writer failed a write of `signals`: its echo demands go stale."""
        with self.lock:
            if self.devices.get(device.name) is device:
                self._writes_failing(device, signals)

    def writes_recovered(self, device: Device) -> None:
        """A blocking device's writer wrote again after failing: its echo demands are known."""
        with self.lock:
            if self.devices.get(device.name) is device:
                self._writes_recovered(device)

    def _writes_failing(self, device: Device, signals: Iterable[Signal]) -> None:
        """While `device`'s writes fail, every echo demand on it is `stale(write_failed)`.

        What the device holds is not known. Each echo demand with a reading
        gets a `stale(write_failed)` reading now -- a limit or a wait that
        follows it fails closed, a chart breaks -- and its last value stays
        its `last_usable`. One never read yet stays `pending`. `signals` (the
        demands in the failed write) are remembered: they stay stale after
        the device recovers, until a demand of theirs commits.
        """
        self._write_lost.setdefault(device, set()).update(
            s for s in signals if s.role is Role.DEMAND
        )
        gone = stale(Reason.WRITE_FAILED)
        by_node: dict[Node, dict[Signal, Any]] = {}
        for signal in _echoes(device):
            reading = self.router.latest.get(signal)
            if reading is None or reading.value == gone:
                continue
            by_node.setdefault(signal.node, {})[signal] = gone
        if by_node:
            now = self.clock.now_ns()
            self.on_samples([Sample(node, now, values) for node, values in by_node.items()])

    def _writes_recovered(self, device: Device) -> None:
        """`device` committed again: its echo demands return to their last value.

        Every echo demand still `stale(write_failed)` gets its `last_usable`
        value back as its reading, except those whose own write was lost
        (in a failed commit, not committed since): nothing they asked for
        reached the device, so they stay stale until a demand of theirs
        commits.
        """
        lost = self._write_lost.get(device, set())
        gone = stale(Reason.WRITE_FAILED)
        by_node: dict[Node, dict[Signal, Any]] = {}
        for signal in _echoes(device):
            reading = self.router.latest.get(signal)
            if reading is None or reading.value != gone or signal in lost:
                continue
            if (usable := self.router.last_usable.get(signal)) is not None:
                by_node.setdefault(signal.node, {})[signal] = usable.value
        if by_node:
            now = self.clock.now_ns()
            self.on_samples([Sample(node, now, values) for node, values in by_node.items()])

    def note_read(self, device: Device, samples: Iterable[Sample]) -> None:
        """What a poll of `device` delivered: its read path, for `stale(device_offline)`."""
        path = self._read_path.setdefault(device, set())
        for sample in samples:
            path.update(sample.values)

    def device_offline(self, device: Device) -> None:
        """`device` went offline: what its reads delivered is `stale(device_offline)` at once.

        Its readouts and sensed demands on its read path (what its polled
        reads have delivered) get a `stale(device_offline)` reading; its
        settings, configs and echo demands keep theirs. A signal never read
        stays `pending`. Each returns to `ok` with its next read.
        """
        with self.lock:
            if self.devices.get(device.name) is not device:
                return
            gone = stale(Reason.DEVICE_OFFLINE)
            by_node: dict[Node, dict[Signal, Any]] = {}
            for signal in self._read_path.get(device, ()):
                if signal.role is Role.READOUT or (
                    signal.role is Role.DEMAND and signal.spec.readback is Readback.SENSED
                ):
                    by_node.setdefault(signal.node, {})[signal] = gone
            if by_node:
                now = self.clock.now_ns()
                self.on_samples([Sample(node, now, values) for node, values in by_node.items()])

    def _states(
        self, device: Committable, time_ns: int, before: Mapping[Signal, int]
    ) -> dict[Signal, WriteState]:
        """What a commit set each staged demand to, with what the rig knows; clears `staged`.

        The driver may have pushed a readback in `commit` (`before` is the
        router's count per signal from before it ran): that is the state's
        value. A demand it did not push gets the committed value as its
        reading, so every demand has a current value. The rig adds what was
        asked for, when the clamp changed it, and which controller drives
        the signal; the device's `written` gets the filled-in state too, so
        the wire shows one thing.

        A demand the driver's `commit` never read (a composite that drives
        from its target and ignores its line demands while blending) was not
        set: it is not echoed as a reading, its state keeps the reading
        as it was (None with none) with the demand as `requested`, and a
        `demand_ignored` event says so, once until one is read again.
        """
        states: dict[Signal, WriteState] = {}
        seq = self.router.seq
        staged = device.staged
        unread = set(staged.unread()) if isinstance(staged, Staged) else set()
        for signal, value in dict.items(staged):
            pushed = seq.get(signal, 0) != before.get(signal, 0)  # the driver's readback
            requested = self._requested.pop(signal, None)
            ignored = signal in unread
            if ignored:
                self._demand_ignored(device, signal, value)
                requested = value if requested is None else requested
                reading = self.router.latest.get(signal)
                value = None if reading is None or not reading.usable else reading.value
            else:
                self._ignored.discard(signal)
                if (lost := self._write_lost.get(device)) is not None:
                    lost.discard(signal)  # it committed: made good
            if pushed:
                reading = self.router.latest.get(signal)
                value = None if reading is None or not reading.usable else reading.value
            at_limit = signal.at_limit
            if at_limit is None and (limits := signal.limits) is not None and value is not None:
                if value <= limits[0]:
                    at_limit = Limit.LOW
                elif value >= limits[1]:
                    at_limit = Limit.HIGH
            holder = self.controllers.driving(signal)
            state = WriteState(
                value=value,
                requested=requested,
                at_limit=at_limit,
                controller=None if holder is None else holder.name,
            )
            device.written[signal] = states[signal] = state
            if self.write_states.watched:
                self.write_states.set(signal.address, state)
            signal.at_limit = None
            if not pushed and not ignored:  # no readback: the value stands, with its mark
                self.router.push(
                    Sample(signal.node, time_ns, {signal: value}, _mark(signal, at_limit))
                )
            elif pushed and at_limit is not None:
                self._mark_pushed(signal, at_limit)
            # Fold the write record into the reading itself, so it rides the samples stream
            # with the value instead of a separate `/ws/writes` cell.
            if (reading := self.router.latest.get(signal)) is not None:
                self.router.latest[signal] = replace(
                    reading,
                    requested=state.requested,
                    at_limit=state.at_limit,
                    controller=state.controller,
                )
        device.staged.clear()
        return states

    def _mark_pushed(self, signal: Signal, at_limit: Limit) -> None:
        """Mark a driver's readback, still waiting in `_pushed`, with the rig's `at_limit`."""
        for i in range(len(self._pushed) - 1, -1, -1):
            sample = self._pushed[i]
            if signal in sample.values:
                if sample.marks.get(signal) is None:
                    marks = {**sample.marks, signal: at_limit}
                    self._pushed[i] = Sample(sample.node, sample.time_ns, sample.values, marks)
                return

    def _demand_ignored(self, device: Committable, signal: Signal, value: float) -> None:
        if signal in self._ignored:
            return
        self._ignored.add(signal)
        self.event(
            Severity.WARNING,
            Scope.DEVICE,
            device.name,
            Code.DEMAND_IGNORED,
            f"'{signal.address}': {value} was not read by the driver's commit; nothing was set",
            {"signal": signal.address, "demand": value},
        )

    def _flush_pushed(self) -> None:
        """Deliver what commits pushed, each batch as one more delivery, until nothing is left.

        One chain: a controller already stepped in it is not stepped again
        on a reading its own commit pushed (its output's device reading its
        measured signal back). The reading still lands; the controller steps on the
        next delivery that starts a chain.
        """
        outer = self._stepped
        if outer is None:
            self._stepped = set()
        try:
            while self._pushed:
                pushed, self._pushed = self._pushed, []
                self._deliver_samples(pushed, noted=True)
        finally:
            if outer is None:
                self._stepped = None

    def written(self, device: Committable, time_ns: int, before: Mapping[Signal, int]) -> None:
        """A blocking device's writer finished a commit: publish, deliver and record its states.

        `before` is the router's count per signal from just before the
        commit ran, as in `_states`: a signal counted since was the driver's
        readback.

        A device removed while the write was in flight is not the rig's any
        more: its states are dropped.
        """
        with self.lock:
            if self.devices.get(device.name) is not device:
                return
            self._touched = {}  # the context for the readbacks `_states` pushes
            try:
                filled = self._states(device, time_ns, before)
            finally:
                self._touched = None
            self._deliver(filled)
            if filled and self.recorder is not None:
                self.recorder.record((), (), filled, time_ns=time_ns)
            self._flush_pushed()

    def _deliver(self, states: Mapping[Signal, WriteState]) -> None:
        """Close each controller's tick with the state its output was committed to."""
        for signal, state in states.items():
            if (controller := self.controllers.driving(signal)) is not None:
                controller.delivered(state)
                if self.controller_states.watched:
                    self.controller_states.set(controller.name, controller.state)

    # endregion

    # region Composition

    def _changed(self, reason: str) -> None:
        if self.on_change is not None:
            self.on_change(reason)

    def add_link(self, name: str, config: Any) -> Any:
        """Build a link from its config (a typed `Config`) and hold it under `name`.

        Raises:
            ConflictError: The name is already a link's.
        """
        with self.lock:
            if name in self.links:
                raise ConflictError(f"Link {name!r} already exists")
            self.links[name] = built = config.build()
            self.link_entries[name] = config
            self._changed(f"added link {name}")
            return built

    def remove_link(self, name: str) -> None:
        """Drop a link no device is built on.

        Raises:
            NotFoundError: No such link.
            ConflictError: A device was built on it.
        """
        with self.lock:
            if name not in self.links:
                raise NotFoundError(f"Link {name!r} not found")
            built = self.links[name]
            users = [
                d.name for d in self.devices.values() if any(h is built for h in vars(d).values())
            ]
            if users:
                raise ConflictError(f"Link {name!r} is used by {', '.join(users)}")
            del self.links[name]
            self.link_entries.pop(name, None)
            self._changed(f"removed link {name}")

    def add_entry(self, name: str, entry: DeviceEntry, *, start: bool = True) -> Device:
        """Build `entry` as device `name` and put it on the running rig.

        The four steps a rig file's device gets, at once: build on the rig's
        links, add, bind its inputs, start polling. Nothing is left behind
        if a step fails. A recording in progress records it from now on.

        Raises:
            ConflictError: The name is taken.
            NotFoundError: The driver or a link is not known.
            AddressNotFoundError: A bound address does not resolve.
        """
        with self.lock:
            if name in self.devices:
                raise ConflictError(f"Device {name!r} already exists")
            device = entry.build(name, self.links)
            self.add_device(device)
            try:
                if entry.inputs:
                    self.bind_inputs(device, entry.inputs)
            except Exception:
                self._drop_device(device)
                raise
            self.entries[name] = entry
            if start:
                self.start_polling(device)
            if self.recorder is not None:
                self.recorder.declare(device.signals.values())
            self._changed(f"added device {name}")
            return device

    def remove_device(self, name: str) -> None:
        """Take a device off the running rig, and everything that hung off it.

        Its poll loop stops; controllers driving or regulating a signal of
        it are detached; other devices' inputs bound into it are unbound;
        its readings leave the router and the streams.

        Raises:
            NotFoundError: No such device.
        """
        with self.lock:
            device = self.devices.get(name)
            if device is None:
                raise NotFoundError(f"Device {name!r} not found")
            for cname, controller in list(self.controllers.items()):
                if (
                    controller.output_signal.device is device
                    or controller.measured_signal.device is device
                ):
                    self.detach_controller(cname)
            self._drop_device(device)
            self.entries.pop(name, None)
            self._changed(f"removed device {name}")

    def _drop_device(self, device: Device) -> None:
        """Undo `add_device` and `bind_inputs`, stop its polling; controllers are the caller's.

        Waits for no thread: the caller holds the lock, which a read or a
        write in flight needs to report. Each finds the device gone and drops
        what it has.
        """
        self.polling.stop(device.name)
        for signal in list(self._observers):
            if signal.device is device:
                del self._observers[signal]
            else:
                self._observers[signal].pop(device, None)
        for node in list(self._node_observers):
            if node.device is device:
                del self._node_observers[node]
            else:
                self._node_observers[node].pop(device, None)
        for other in self.devices.values():
            for role, bound in list(other.bound.items()):
                if bound.device is device:
                    del other.bound[role]
        self._write_lost.pop(device, None)
        self._read_path.pop(device, None)
        for signal in device.signals.values():
            self.router.latest.pop(signal, None)
            self.router.last_usable.pop(signal, None)
            self.router.recent.pop(signal, None)
            self.router.seq.pop(signal, None)
            self.write_states.discard(signal.address)
            self._requested.pop(signal, None)
            self._ignored.discard(signal)
        for signal in device.signals.values():
            self.conditions.clear_owner(signal)
            self.bands.forget(signal)
        self.conditions.clear_owner(device)
        for node in (device.root, *device.root.descendants()):
            self.router.samples.pop(node, None)
            self.router.cuts.pop(node, None)
            self.samples.discard(node.address)
        if (writer := self._writers.pop(device, None)) is not None:
            writer.stop(join=False)  # a write in flight lands in `written`, which drops it
        self.release(device.name)
        if device in self._running:
            device.cancel()  # its long command ends early; what it pushes after goes nowhere
        device.router = Router()
        device.conditions = Conditions(now_ns=lambda: device.router.now_ns())

    def document(self) -> dict[str, Any]:
        """The running rig as a rig file: what `RigConfig` would load to build it again.

        Rendered from what each link and device was built from and how each
        controller is wired now, in the file's canonical form; links and
        devices built in code rather than from an entry are left out.
        """
        from flyball.runtime.config import ControllerEntry, RigConfig

        with self.lock:  # a consistent view: nothing added or removed while it is read
            controllers = {
                name: ControllerEntry(
                    measured=c.measured_signal.address,
                    law=c.law.config if c.law is not None else None,
                    # The file's default: the identity. Left out, as a file would.
                    feedforward=None
                    if c.feedforward.config.type == "identity"
                    else c.feedforward.config,
                    default=self.controllers.default == name,
                    min_period_s=c.min_period_s,
                )
                for name, c in self.controllers.items()
            }
            links = {
                name: {
                    "type": link.type_name,
                    **link.model_dump(mode="json", exclude_none=True, exclude_defaults=True),
                }
                for name, link in self.link_entries.items()
            }
            loaded = {
                "name": self.name,
                **self.header,
                "links": links,
                "devices": dict(self.entries),
                "controllers": controllers,
            }
        config = RigConfig.model_validate(loaded)
        # Defaults left out, as a hand-written file leaves them: a saved rig
        # says what was chosen, not everything a driver could take.
        document = config.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        document["links"] = links
        for key in ("devices", "controllers"):
            document.setdefault(key, {})
        for name, entry in controllers.items():  # a type is a default too; the file needs it
            rendered = document["controllers"][name]
            if entry.law is not None:
                rendered.setdefault("law", {})["type"] = entry.law.type
            if entry.feedforward is not None:
                rendered.setdefault("feedforward", {})["type"] = entry.feedforward.type
        return document

    # endregion

    # region Commands

    def run_command(
        self, device: Device, command: str, args: Mapping[str, Any] | None = None
    ) -> Any:
        """Run `device`'s `command` with `args`, as the rig: linked, clamped, owned, recorded.

        An argument that is a value for a demand (`Annotated[..., d]`) is filled from
        that demand's current value when left out, and clamped to the
        signal's effective limits. A synthesised `set_<name>` goes through
        [demand][flyball.rig.rig.Rig.write]. A command that changes
        what drives the device -- one with a `mode`, or a linked argument --
        is refused while a controller drives one of the device's demands,
        unless it `interrupts`: then the controller is put into manual first,
        with an event. The method runs under the rig lock -- unless it is
        `long` (a dose, a move): then only the checks do, the method runs off
        the lock, so polling, deliveries and the device's `stop` carry on, and
        the rig re-enters the lock after it. Afterwards the
        device's `mode` output (if it has one) becomes the command's, a
        `commit=True` command commits the device, each linked demand the
        driver did not push gets its argument as its reading, and
        `last.<command>` records what ran. Returns what the method returned.

        Raises:
            NotFoundError: No such command.
            ConflictError: A controller drives the device; or a long command
                while the device runs another, or while the caller holds the
                rig lock (it would wait under it).
            NotReadyError: A linked argument was left out and its demand has
                no value yet.
            LimitNotKnownError: A linked argument's demand has a limit that
                follows a signal with no value yet: refused, not run unclamped.
            LimitsInvertedError: A linked argument's demand has limits that
                resolve inverted now: refused.
        """
        try:
            spec = device.commands[command]
        except KeyError as e:
            raise NotFoundError(f"{device.name!r} has no command {command!r}") from e
        given = dict(args or {})
        if spec.demand_of is not None:
            signal = device.signals[spec.demand_of]
            return self.write(signal.node, {signal: given["value"]})
        if spec.long and _held(self.lock):
            raise ConflictError(
                f"{device.name}.{command} waits: it cannot run while the rig lock is held"
            )
        with self.lock:
            linked = self._command_checks(device, spec, command, given)
            if not spec.long:
                return self._run_locked(device, spec, command, given, linked)
            if (running := self._running.get(device)) is not None:
                raise ConflictError(
                    f"'{device.name}' is running {running!r}: stop it, or wait for it to end"
                )
            self._running[device] = command
            device.cancelling.clear()
            before = dict(self.router.seq)
        try:
            result = spec.method(device, **given)
        finally:
            with self.lock:
                self._running.pop(device, None)
        with self.lock:
            if self.devices.get(device.name) is not device:
                return result  # removed while it ran: nothing of it is the rig's any more
            self._touched = {}
            try:
                self._command_ran(
                    device, spec, command, given, linked, before, self.clock.now_ns(), outer=None
                )
            finally:
                self._touched = None
                self._flush_pushed()
        return result

    def _command_checks(
        self, device: Device, spec: CommandSpec, command: str, given: dict[str, Any]
    ) -> dict[str, Signal]:
        """Fill and clamp the linked arguments; refuse, or take over from, a driving controller.

        Under the lock. Returns the linked arguments' signals, by argument.
        """
        linked: dict[str, Signal] = {}
        for name, param in spec.params.items():
            if param.link is None:
                continue
            signal = linked[name] = device.signals[param.link]
            if name not in given:
                given[name] = self.router.value(signal)
            elif isinstance(given[name], (int, float)):
                given[name] = signal.clamp(float(given[name]))
        drives = spec.mode is not None or any(s.role is Role.DEMAND for s in linked.values())
        if drives:
            # It changes what drives the device: not while a controller does.
            # A setting (a blend flow) is not what a controller drives.
            for signal in device.signals.values():
                holder = self.controllers.driving(signal)
                if holder is None or not holder.mode.active():
                    continue
                if not spec.interrupts:
                    raise ConflictError(
                        f"'{signal.address}' is driven by controller {holder.name!r}:"
                        f" {command!r} would fight it; put it in manual, or detach it"
                    )
                holder.manual()
                self.event(
                    Severity.INFO,
                    Scope.CONTROLLER,
                    holder.name,
                    Code.INTERRUPTED,
                    f"put in manual by {device.name}.{command}",
                )
                if self.controller_states.watched:
                    self.controller_states.set(holder.name, holder.state)
        return linked

    def _run_locked(
        self,
        device: Device,
        spec: CommandSpec,
        command: str,
        given: dict[str, Any],
        linked: Mapping[str, Signal],
    ) -> Any:
        """A command that does not wait: the method and what follows it, under the lock."""
        outer = self._touched
        if outer is None:
            self._touched = {}
        time_ns = self.clock.now_ns()
        before = dict(self.router.seq)
        try:
            result = spec.method(device, **given)
            self._command_ran(device, spec, command, given, linked, before, time_ns, outer=outer)
        finally:
            if outer is None:
                self._touched = None
                self._flush_pushed()  # a failed commit's stale demands are delivered too
        return result

    def _command_ran(
        self,
        device: Device,
        spec: CommandSpec,
        command: str,
        given: Mapping[str, Any],
        linked: Mapping[str, Signal],
        before: Mapping[Signal, int],
        time_ns: int,
        *,
        outer: dict[Device, None] | None,
    ) -> None:
        """After the method: `mode`, the linked readings, `last.<command>`, and the commit."""
        if spec.mode is not None and (mode := device.signals.get("mode")) is not None:
            mode.push(spec.mode, time_ns)
        for name, signal in linked.items():
            if self.router.seq.get(signal, 0) == before.get(signal, 0):  # no readback
                signal.push(given[name], time_ns)
        if (last := device.signals.get(f"last.{command}")) is not None:
            last.push({"args": dict(given), "at": time_ns}, time_ns)
        if spec.commit:
            if outer is not None:
                outer[device] = None
            else:
                states = self._commit((device,), time_ns)
                if states and self.recorder is not None:
                    self.recorder.record((), (), states, time_ns=time_ns)

    # endregion

    # region Controllers

    def attach_controller(
        self,
        output: Signal,
        measured: Signal,
        *,
        law: ControlLawLike | str | None = None,
        feedforward: FeedforwardLike | str | None = None,
        default: bool = False,
        min_period_s: float | None = None,
    ) -> Controller:
        """Regulate `measured` through `output`; the controller is named by `output`'s address.

        Args:
            output: The demand driven.
            measured: The P signal regulated.
            law: The control law, a config, or a stored tuning's name.
            feedforward: What maps the setpoint to a value in the output's
                unit: an instance, a config, or a type. Default: the setpoint
                itself when the units agree, else none.
            default: Make this the controller commands address when they name none.
            min_period_s: Step the law at most this often.

        Raises:
            SignalClaimedError: `output` is already driven, or `measured`
                already regulated, by another controller.
            ConflictError: `output` is not writable, `measured` not published,
                or the feedforward cannot map the units.
        """
        if isinstance(law, str):
            law = self.tunings.get(law)
        if isinstance(feedforward, str):
            feedforward = get_catalog().feedforwards[feedforward]()

        def write(value: float) -> float | None:
            states = self.write(output.node, {output: value}, by=controller)
            return None if (state := states.get(output)) is None else state.value

        with self.lock:  # not while a delivery is looking controllers up
            controller = Controller(
                self.clock,
                output,
                measured,
                law=law,
                feedforward=feedforward,
                min_period_s=min_period_s,
                write=write,
                hold=lambda: self.hold_reason(controller),
            )
            self.controllers.add(controller, default=default)
            self._changed(f"attached controller {controller.name}")
            return controller

    def detach_controller(self, name: str) -> Controller:
        """Take the controller off its output: manual demands may drive it again.

        The controller is left in manual with nothing to write to.

        Raises:
            ControllerNotFoundError: No controller of that name.
        """
        with self.lock:
            controller = self.controllers.remove(name)
            self._fresh.pop(controller, None)
            self.conditions.clear_owner(controller, reason="detached")
            controller.manual()
            controller.write = Controller._unwired
            controller.hold = Controller._never_held
            # A watcher primes from this cell; a name the rig no longer has must not be in it.
            self.controller_states.discard(name)
            self._changed(f"detached controller {name}")
            return controller

    # endregion

    # region Delivery

    def on_samples(self, samples: Sequence[Sample]) -> None:
        """One delivery: observers, then the controllers, one commit per touched device.

        A sample's keys must be bound, readable signals under its node, and
        there must be some: anything else is a driver bug, refused before
        any of the delivery is applied. Every reading lands in `latest`;
        only those on published signals go on to `samples` and the
        recorder, so a fresh read of a setting is known here without being
        streamed. Several controllers on one device, and a bound input
        beside them, cost that device one commit.

        Every value goes through the value gate first
        ([normalised][flyball.foundation.device.signal.normalised]): `None`,
        NaN and the infinities become `invalid` no-values, a `railed` value
        its number and its `at_limit` mark.

        Raises:
            ValueError: A sample carries a stray key, or none.
        """
        samples = [normalised(sample) for sample in samples]
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
            self._stepped = set()
            try:
                self._deliver_samples(samples)
                self._flush_pushed()
            finally:
                self._stepped = None

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
                        # The cell keeps one sample per node; a device pushes
                        # several on its root within one flush (a readback,
                        # a mode, a command's record), so merge rather than
                        # replace: the newest value of every signal.
                        held = self.samples.get(sample.node.address)
                        if held is not None and held.time_ns <= streamed.time_ns:
                            marks = {
                                s: m for s, m in held.marks.items() if s not in streamed.values
                            }
                            streamed = Sample(
                                streamed.node,
                                streamed.time_ns,
                                {**held.values, **streamed.values},
                                {**marks, **streamed.marks},
                            )
                        self.samples.set(sample.node.address, streamed)
                messages: dict[tuple[Device, Node], None] = {}
                for reading in sample.readings():
                    signal = reading.signal
                    self.bands.check(reading)  # noted: its band condition, raised or cleared
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
                    if (controller := self.controllers.find(signal)) is not None and (
                        self._stepped is None or controller not in self._stepped
                    ):
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
                if self._stepped is not None:
                    self._stepped.add(controller)
                self._step(controller, reading)
            time_ns = max(s.time_ns for s in samples)
            failed: dict[Signal, WriteState] = {}
            states = self._commit(touched, time_ns, failed)
        finally:
            self._touched = None
        self._deliver(failed)
        self._deliver(states)
        if self.controller_states.watched:
            for controller, _ in ticks:
                self.controller_states.set(controller.name, controller.state)
        if self.recorder is not None:
            self.recorder.record(published, ticks, states, time_ns=time_ns)

    def _step(self, controller: Controller, reading: Reading) -> None:
        """One controller's step, kept from the rest of the delivery.

        A law that raises (no law set, a NaN it cannot take) would otherwise
        abort the whole delivery: every other controller's step, the commits,
        the readings, the recorder and the stream. It becomes an event
        instead, and the controller's mode is left as it was -- what a
        faulted controller should do is a separate decision.
        """
        self._fresh[controller] = self._fresh.get(controller, 0) + 1 if reading.usable else 0
        try:
            controller.on_reading(reading)
        except Exception as error:
            raised = self.conditions.set(
                controller,
                Code.STEP_FAILED,
                Severity.ERROR,
                f"{type(error).__name__}: {error}",
                {"measured": reading.signal.address},
            )
            if raised:  # the traceback once per outage, not once per step
                log.exception("controller %s failed its step", controller.name)
            return
        self.conditions.clear(controller, Code.STEP_FAILED, message="stepping again")

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


def _echoes(device: Device) -> Iterator[Signal]:
    """`device`'s demands whose reading is the value the rig committed (`readback: echo`)."""
    for signal in device.signals.values():
        if signal.role is Role.DEMAND and signal.spec.readback is Readback.ECHO:
            yield signal


def _mark(signal: Signal, at_limit: Limit | None) -> dict[Signal, Limit]:
    """The marks of a one-value sample: its `at_limit`, if any."""
    return {} if at_limit is None else {signal: at_limit}
