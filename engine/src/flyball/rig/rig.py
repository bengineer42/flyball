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
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock, RLock
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
    InputBinding,
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
from flyball.foundation.device.values import Values
from flyball.foundation.errors import ConflictError, NotFoundError, NotReadyError
from flyball.foundation.router import RECENT_READINGS, Latest, Router, Topic
from flyball.foundation.time import Timer, Timers
from flyball.foundation.typing import OrderedSet
from flyball.library.tunings import Tunings
from flyball.model.catalog import get_catalog
from flyball.model.controller import Controller, ControllerState
from flyball.model.feedforward import FeedforwardLike
from flyball.model.law import ControlLawLike
from flyball.runtime.writer import Writer

from .bands import Bands
from .controllers import Controllers
from .faults import Faults
from .liveness import Liveness
from .polling import Polling, poll_period
from .stopping import Actor, Stopping, resolve_output
from .triggers import Triggers
from .values import LiveValues

if TYPE_CHECKING:
    from flyball.foundation.device import Permissive
    from flyball.model.controller import OnFault
    from flyball.record import Store
    from flyball.runtime.recorder import Recorder

log = logging.getLogger("flyball.rig")

RESUME_AFTER = 3
"""Readings with a value, in a row, before a controller frozen on a no-value steps again."""

RETRY_FIRST_S = 5.0
"""The first write retry comes after `min(poll_s, RETRY_FIRST_S)`; this for a device with no
`poll_s` (write-only). Each retry after doubles it, up to `RETRY_MAX_S`."""
RETRY_MAX_S = 60.0
RETRY_MAX_AGE_S = 60.0
"""A staged value older than this (the device's `retry_max_age_s`) is dropped, not sent."""

SETPOINT_PERIOD_MIN_S = 0.1
"""The shortest default period a moving setpoint's feedforward is re-applied on."""

REPLACE_WAIT_S = 1.0
"""How long a stop waits for the rig's lock to drop staged values before leaving it to the
latch (which refuses the commit anyway) and to the stop's own write."""

FRESH_READ_WAIT_S = 5.0
"""How long a fresh read waits for a read of the same device already in flight (a poll,
another fresh read) before it is refused."""


def _held(lock: RLock) -> bool:
    """Whether the calling thread holds `lock` (an `RLock`, or a test's wrapper of one)."""
    return lock._is_owned()  # type: ignore[attr-defined]  # CPython's RLock has no public form


@dataclass(frozen=True, slots=True)
class Interrupted:
    """A controller a command put into manual: its name, and the mode it was in."""

    controller: str
    was: str


@dataclass(frozen=True, slots=True)
class CommandRun:
    """What a command returned, and the controllers it put into manual (`interrupts`)."""

    result: Any
    interrupted: tuple[Interrupted, ...] = ()


def _not_writable(signal: Signal) -> str:
    """Why a write to `signal` is refused, naming the command that moves it if one does.

    A demand that is only a readback (`[RP]`: a blender's `flows.dry`) is moved
    by the command whose argument is linked to it -- the one that sets it to a
    value -- or, failing one, by a command that declares it in `writes=`; the
    message names that command, and says when it displaces a regulating
    controller.
    """
    refused = f"'{signal.address}' [{signal.access}] is not writable"
    if signal.role is not Role.DEMAND:
        return refused
    path = str(signal.path)
    commands = [s for s in signal.node.device.commands.values() if s.demand_of is None]
    movers = [s for s in commands if any(p.link == path for p in s.params.values())] or [
        s for s in commands if path in s.writes
    ]
    if not movers:
        return refused
    named = " or ".join(repr(spec.name) for spec in movers)
    note = (
        " (it puts a regulating controller in manual)"
        if all(spec.interrupts for spec in movers)
        else ""
    )
    return f"{refused}: it is a readback, moved by the command {named}{note}"


class _Retry:
    """A device whose writes fail: the retry armed on the rig clock, and its interval now."""

    __slots__ = ("interval_s", "kept", "timer")

    def __init__(self, interval_s: float) -> None:
        self.interval_s = interval_s
        self.timer: Timer | None = None
        self.kept: dict[Signal, float] = {}
        """What failed writes kept, by signal: a commit that sets one of these re-sent it."""


class Rig:
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
    liveness: Liveness
    """Each judged signal's stale deadline, on the rig clock: `stale(silent | never_read |
    last_read)` pushed when nothing arrives within its threshold."""
    faults: Faults
    """Each regulated source's outage: fault time accrued on the rig clock, and its release."""
    stopping: Stopping
    """The stops and latches: what refuses a write, what a stop writes, Reset, `on_fault`."""
    permissives: dict[Signal, tuple[Permissive, Signal]]
    """Each demand with a `permissive`, and the signal whose value permits a write to it."""
    _forcing: set[Device]
    """Devices a stop, or a person's write under the rig stop, is committing now: their commit
    goes through the latch."""
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
    _followers: dict[Signal, OrderedSet[InputBinding]]
    """Every input binding that follows a signal, by that signal: a device's, a controller's,
    a program step's."""
    _node_followers: dict[Node, OrderedSet[InputBinding]]
    """Input bindings that follow a whole node, by that node: a sample with something published
    under it reaches them."""
    values: LiveValues
    """Where each `driver: values` signal's value came from, and the store row that keeps its
    last write across restarts."""
    _written_by: dict[Signal, str | None]
    """Who asked for each staged write, until its commit: a values signal's writer."""
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
    _retries: dict[Device, _Retry]
    """Per device whose writes fail: its retry on the rig clock."""
    _staged_ns: dict[Signal, int]
    """When each demand staged for an inline commit was asked for: a failed one's age."""
    _reapplying: dict[Controller, Timer]
    """Per controller following a moving setpoint: its re-apply on the rig clock (E25)."""
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
        self._clock = Clock()
        self._timers: Timers | None = None
        self._timers_lock = Lock()
        self._closed = False
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
        self.bands = Bands(self.conditions, lambda: self.clock.now_ns(), self.after, self.lock)
        self.liveness = Liveness(self)
        self.faults = Faults(self)
        self.stopping = Stopping(self)
        self.faults.on_fault.append(self.stopping.on_fault)
        self.permissives = {}
        self._forcing = set()
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
        self._followers = {}
        self._node_followers = {}
        self.values = LiveValues(self)
        self._written_by = {}
        self._requested = {}
        self._touched = None
        self._stepped = None
        self._ignored = set()
        self._running = {}
        self._write_lost = {}
        self._read_path = {}
        self._fresh = {}
        self._retries = {}
        self._staged_ns = {}
        self._reapplying = {}
        self.entries = {}
        self.link_entries = {}
        self.files = []
        self.header = {}
        self.loaded = None
        self.saved_overlay = {}
        self.on_change = None
        self.on_recording_stopped = None

    @property
    def clock(self) -> Clock:
        """The rig's timebase: wall time, or a sim's scaled or stepped clock.

        Swap it before anything is armed on it (`RunnerConfig.build` does, right after
        `Rig()`): the timers are rebuilt on the new clock, and what was armed on the old is
        cancelled.
        """
        return self._clock

    @clock.setter
    def clock(self, clock: Clock) -> None:
        with self._timers_lock:
            self._clock = clock
            old, self._timers = self._timers, None
        if old is not None:
            if old.pending:
                log.warning("the rig's clock was swapped with %d timers armed", old.pending)
            old.close()

    # region Timers

    def _timers_now(self) -> Timers | None:
        """The rig's timers, made on first use on the clock now; None once the rig is closed."""
        with self._timers_lock:
            if self._closed:
                return None
            if self._timers is None:
                self._timers = Timers(self._clock, f"timers:{self.name or 'rig'}")
            return self._timers

    @property
    def timers(self) -> Timers:
        """One-shot and periodic calls on the rig clock: liveness, retries, fault waits.

        Raises:
            RuntimeError: The rig is closed.
        """
        if (timers := self._timers_now()) is None:
            raise RuntimeError("the rig is closed")
        return timers

    def after(self, seconds: float, fn: Callable[[], object], name: str = "") -> Timer | None:
        """Run `fn` once, `seconds` of the rig's time from now; None once the rig is closed.

        `fn` runs on the timers' thread (or whoever advances a stepped clock),
        holding no lock: it takes the rig's itself if it needs it.
        """
        timers = self._timers_now()
        if timers is None:
            return None
        try:
            return timers.after(seconds, fn, name)
        except RuntimeError:  # closed meanwhile
            return None

    def every(self, seconds: float, fn: Callable[[], object], name: str = "") -> Timer | None:
        """Run `fn` every `seconds` of the rig's time, first after one; None once closed."""
        timers = self._timers_now()
        if timers is None:
            return None
        try:
            return timers.every(seconds, fn, name)
        except RuntimeError:
            return None

    # endregion

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
        self.liveness.watch(device, polled=False)  # its own `stale_after_s`: judged from now
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
        with self._timers_lock:
            if self._closed:
                return
            self._closed = True
            timers, self._timers = self._timers, None
        if timers is not None:  # first: nothing armed fires while the rig comes down
            timers.close()
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
        device, except those the rig file marks `record: false`, and every
        controller. Replaces a running recorder, closing its session first.
        `session` is what the store's `open_session` takes; a `start_ns` in
        it backdates the session (for what is then backfilled), else it
        starts now.
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
                    if (Access.P in s.access or Access.W in s.access) and s.spec.record
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

    def bind_inputs(self, device: Device, inputs: Mapping[str, str | float]) -> None:
        """Bind `device`'s inputs: the rig file's `inputs:`, name -> an address or a number.

        Call after every device is added: an address may name a device
        declared later in the file. Each becomes the device's
        [InputBinding][flyball.foundation.device.binding.InputBinding] for that
        name (`device.bound`), through [bind][flyball.rig.rig.Rig.bind]. From
        then on, every delivery that brings a bound signal a reading -- or,
        for a bound node, a sample with something published under it --
        reaches the device in that delivery: `Device.inputs_changed`, before
        the controllers step, then its `commit` if it has demands. Inputs that
        have a value already (a number, a source read before) are delivered to
        it the same way once, now.

        Every input the driver declares must be given one, and a driver that
        declares its inputs takes no other names: an input has no default.

        Raises:
            AddressNotFoundError: An address does not resolve; names it.
            ConflictError: A declared input left out, or a name the driver does
                not declare; a signal that does not publish, or a node with
                nothing published under it; a cycle through `inputs:`, named
                (nothing of this call stays bound).
        """
        declared = type(device).INPUTS
        if declared:
            if missing := [name for name in declared if name not in inputs]:
                raise ConflictError(
                    f"{device.name}: input {', '.join(repr(n) for n in missing)} is neither"
                    " bound nor a number: give `inputs: {"
                    + ", ".join(f"{n}: <address or number>" for n in missing)
                    + "}`"
                )
            if unknown := [name for name in inputs if name not in declared]:
                raise ConflictError(
                    f"{device.name}: {', '.join(repr(n) for n in unknown)} is not an input of"
                    f" its driver; it has {', '.join(repr(n) for n in declared)}"
                )
        with self.lock:
            bound: list[InputBinding] = []
            try:
                for name, source in inputs.items():
                    bound.append(self.bind(device.binding(name), source))
            except Exception:
                for binding in bound:
                    self.unbind(binding)
                raise
            known = [b for b in bound if b.constant is not None or b.reading is not None]
            if known:
                self._first_values(device, known)

    def bind(self, binding: InputBinding, source: str | float | Signal | Node) -> InputBinding:
        """Point `binding` at `source`: an address, a signal or node, or a number. Returns it.

        What `inputs:` is made of, and what anything else that follows a
        signal holds: a controller, a program's `settle` step. An address is
        resolved here, once; a signal must publish, a node have something
        published under it. Bound again, it leaves what it followed.
        Readings on the source reach the binding's watchers, and a device
        that owns it, in the delivery they arrive in.

        Raises:
            AddressNotFoundError: An address does not resolve.
            ConflictError: A signal that does not publish; a node with nothing
                published under it; for a device's input, a cycle through
                inputs back to that device, named (the binding is left unbound).
            ValueError: A number that is not finite.
        """
        if isinstance(source, str):
            target: Signal | Node | float = self.resolve(source)
        else:
            target = source
        with self.lock:
            if isinstance(target, Signal):
                if Access.P not in target.access:
                    raise ConflictError(
                        f"{binding.where}: '{target.address}' [{target.access}] is not published"
                    )
            elif isinstance(target, Node):
                if not any(Access.P in s.access for s in target.walk()):
                    raise ConflictError(
                        f"{binding.where}: nothing under '{target.address}' publishes"
                    )
            elif isinstance(target, bool) or not math.isfinite(float(target)):
                raise ValueError(f"{binding.where}: {target!r} is not a finite number")
            self.unbind(binding)
            binding.attach(target)
            if isinstance(owner := binding.owner, Device) and (cycle := self._cycle_through(owner)):
                path = "; ".join(f"{b.where} <- {b.address}" for b in cycle)
                binding.detach()
                raise ConflictError(f"a cycle through inputs: {path}")
            if isinstance(target, Signal):
                self._followers.setdefault(target, {})[binding] = None
            elif isinstance(target, Node):
                self._node_followers.setdefault(target, {})[binding] = None
            return binding

    def follow(
        self, source: str | float | Signal | Node, *, owner: object, name: str
    ) -> InputBinding:
        """A new binding of `owner`'s input `name` to `source`: for a holder that is not a device.

        A program's `settle` step, a controller: it reads the binding, or
        [watches][flyball.foundation.device.binding.InputBinding.watch] it, and
        [unbinds][flyball.rig.rig.Rig.unbind] it when done.
        """
        return self.bind(InputBinding(owner, name), source)

    def unbind(self, binding: InputBinding) -> None:
        """Stop `binding` following what it follows: it is unbound (`pending`) until bound again."""
        with self.lock:
            source = binding.source
            if isinstance(source, Signal):
                followers = self._followers.get(source)
                if followers is not None:
                    followers.pop(binding, None)
                    if not followers:
                        del self._followers[source]
            elif isinstance(source, Node):
                followers = self._node_followers.get(source)
                if followers is not None:
                    followers.pop(binding, None)
                    if not followers:
                        del self._node_followers[source]
            binding.detach()

    def consumers(self, signal: Signal) -> list[InputBinding]:
        """Every input binding that consumes `signal`: follows it, or a node above it.

        The reverse of each device's `bound`: "who uses this signal". Devices'
        inputs, and any other holder's (a controller, a program step).
        """
        with self.lock:
            found = list(self._followers.get(signal, ()))
            node: Node | None = signal.node
            while node is not None:
                found.extend(self._node_followers.get(node, ()))
                node = node.parent
            return found

    def _cycle_through(self, start: Device) -> list[InputBinding] | None:
        """A path of device inputs from `start` back to itself, if one exists; None if not."""
        stack: list[tuple[Device, list[InputBinding]]] = [(start, [])]
        seen: set[Device] = set()
        while stack:
            device, path = stack.pop()
            for binding in device.bound.values():
                if (source := binding.source) is None:
                    continue
                follows = source.device
                if follows is start:
                    return [*path, binding]
                if follows not in seen:
                    seen.add(follows)
                    stack.append((follows, [*path, binding]))
        return None

    def _first_values(self, device: Device, bindings: list[InputBinding]) -> None:
        """Deliver inputs that have a value already to `device` once, as a delivery would.

        Its `inputs_changed`, then its commit if it has demands; what they push is
        delivered in the same chain.
        """
        now = self.clock.now_ns()
        for binding in bindings:
            binding.notify()
        if self._touched is not None:  # inside a delivery: it commits the device at its end
            self._touched[device] = None
            self._inputs_changed(device, now, bindings)
            return
        touched: dict[Device, None] = {device: None}
        outer = self._stepped
        if outer is None:
            self._stepped = set()
        self._touched = touched
        failed: dict[Signal, WriteState] = {}
        try:
            self._inputs_changed(device, now, bindings)
            states = self._commit(touched, now, failed)
        finally:
            self._touched = None
        try:
            self._deliver(failed)
            self._deliver(states)
            if states and self.recorder is not None:
                self.recorder.record((), (), states, time_ns=now)
            self._flush_pushed()
        finally:
            if outer is None:
                self._stepped = None

    def _inputs_changed(self, device: Device, time_ns: int, bindings: list[InputBinding]) -> None:
        """`device.inputs_changed`, kept from the delivery: a driver that raises is logged."""
        try:
            device.inputs_changed(time_ns, bindings)
        except Exception:
            log.exception("%s: inputs_changed failed", device.name)

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
        self,
        node: Node,
        values: Mapping[str | Signal, float],
        *,
        by: Controller | None = None,
        writer: str | None = None,
        actor: Actor | None = None,
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
        this returns nothing. `writer` says who asked (the principal's `sub`):
        a `driver: values` signal's write is logged and kept under it; `actor`
        is who asked, whole, when a request came in (its `sub` is the writer).

        A latch refuses it: a fault's latch on the signal or its device refuses
        everyone; the rig stop refuses every automatic writer and lets a
        person's write (`actor.person`) through, logged
        (`written_while_stopped`), the latch kept. A controller's write under a
        latch is held, not refused. A `permissive` that does not hold refuses
        it too (a write of the signal's resolved stop value excepted).

        Raises:
            AddressNotFoundError: A name does not resolve under `node`.
            ConflictError: A key is not a writable signal under `node`, or
                is driven by a controller; a latch or a permissive refuses it.
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
        if writer is None and actor is not None:
            writer = actor.sub
        person = actor is not None and actor.person
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
        forced = None
        for signal, value in resolved.items():
            if Access.W not in signal.access:
                raise ConflictError(_not_writable(signal))
            refused, through = self.stopping.refusal(signal, by=by, person=person)
            if refused is not None:
                if by is not None:
                    return {}  # a controller under a latch is held, not failed
                raise ConflictError(refused)
            forced = forced or through
            if (why := self._not_permitted(signal, value)) is not None:
                raise ConflictError(why)
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
            thread = self._writer_for(device)
            for signal, value in clamped.items():
                if thread is None:
                    device.apply(signal, time_ns, value)
                    self._staged_ns[signal] = time_ns  # a newer demand replaces a staged one
                else:
                    thread.apply(signal, time_ns, value)
            for signal in clamped:  # a newer demand supersedes an earlier clamped one
                self._written_by[signal] = writer
                if signal in requested:
                    self._requested[signal] = requested[signal]
                else:
                    self._requested.pop(signal, None)
            if forced is not None:
                for signal, value in clamped.items():
                    self.event(
                        Severity.WARNING,
                        Scope.SIGNAL,
                        signal.address,
                        Code.WRITTEN_WHILE_STOPPED,
                        f"written {value:g} by {writer} while the rig is stopped; still stopped",
                        {"value": value, "by": writer},
                    )
            if self._touched is not None:  # inside a delivery: committed at its end
                self._touched[device] = None
                return {}
            if forced is not None:
                self._forcing.add(device)
            try:
                states = self._committing((device,), time_ns)
                if states and self.recorder is not None:
                    self.recorder.record((), (), states, time_ns=time_ns)
            finally:  # a failed commit's stale demands are delivered too
                self._forcing.discard(device)
                self._flush_pushed()
            return states

    def _not_permitted(self, signal: Signal, value: float | None) -> str | None:
        """Why `signal`'s `permissive` refuses a write of `value` now, or None.

        It fails closed: a permitting signal with no reading, or none with a value,
        refuses. A write of the signal's resolved stop value is always permitted (what
        turns an output off is never refused); `value` None asks for any write.
        """
        held = self.permissives.get(signal)
        if held is None:
            return None
        permissive, source = held
        if value is not None and value == resolve_output(signal).value:
            return None
        reading = self.router.latest.get(source)
        if reading is None or not reading.usable:
            said = "nothing read" if reading is None else f"no value ({reading.quality.value})"
            return (
                f"'{signal.address}' is not permitted: {source.address} has {said}, and a"
                " permissive fails closed"
            )
        if not permissive.holds(float(reading.value)):
            return (
                f"'{signal.address}' is not permitted: needs {permissive.describe()},"
                f" is {reading.value:g}"
            )
        return None

    def permit(self, signal: Signal, permissive: Permissive) -> None:
        """Refuse writes to `signal` unless `permissive` holds (the rig file's `permissive:`).

        Raises:
            AddressNotFoundError: Its signal does not resolve.
            ConflictError: It names a namespace, or a signal that does not publish.
        """
        source = self.resolve(permissive.signal)
        if not isinstance(source, Signal):
            raise ConflictError(
                f"permissive on '{signal.address}': '{source.address}' is a namespace"
            )
        if Access.P not in source.access:
            raise ConflictError(
                f"permissive on '{signal.address}': '{source.address}' [{source.access}] is not"
                " published"
            )
        with self.lock:
            self.permissives[signal] = (permissive, source)

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
        if controller.output_signal in self.permissives:
            if (why := self._not_permitted(controller.output_signal, None)) is not None:
                permissive, source = self.permissives[controller.output_signal]
                self.conditions.set(
                    controller,
                    Code.NOT_PERMITTED,
                    Severity.WARNING,
                    f"{why}: held",
                    {"signal": source.address, "permissive": permissive.describe()},
                )
                return Code.NOT_PERMITTED
            self.conditions.clear(
                controller, Code.NOT_PERMITTED, message="permitted: writing again"
            )
        return None

    def _limit_unknown(
        self, controller: Controller, error: LimitNotKnownError | LimitsInvertedError
    ) -> None:
        """Hold `limit_unknown` on the controller: raised once, not per step.

        `info` while every unknown bound is only `pending` (an input not read yet: benign,
        A3), `warning` when one is `stale` or `invalid` (a fault). `details.why` gives each
        unknown bound's `[quality, reason]`.
        """
        benign = isinstance(error, LimitNotKnownError) and error.benign
        self.conditions.set(
            controller,
            Code.LIMIT_UNKNOWN,
            Severity.INFO if benign else Severity.WARNING,
            f"{error}: held",
            {
                "signal": error.address,
                "unknown": error.unknown,
                "why": getattr(error, "why", {}),
            },
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

    def replace_staged(self, devices: Iterable[Device], signals: Iterable[Signal] = ()) -> None:
        """Drop what is staged on `devices` (whole) and on `signals`: a stop or a latch replaces it.

        What a failed write kept is dropped too, and its retry cancelled (A6). Takes the
        rig's lock, but gives up after `REPLACE_WAIT_S` rather than wait behind a stuck
        delivery: the latch already refuses the commit, and a stop replaces what it writes.
        """
        if not self.lock.acquire(timeout=REPLACE_WAIT_S):
            log.warning("a stop could not take the rig's lock to drop staged values")
            return
        try:
            for device in devices:
                self._drop_staged(device, None)
            by_device: dict[Device, set[Signal]] = {}
            for signal in signals:
                by_device.setdefault(signal.device, set()).add(signal)
            for device, held in by_device.items():
                self._drop_staged(device, held)
        finally:
            self.lock.release()

    def _drop_staged(self, device: Device, signals: set[Signal] | None) -> None:
        if not isinstance(device, Committable):
            return
        staged = device.staged
        chosen = (
            list(dict.keys(staged))
            if signals is None
            else [s for s in signals if s in dict.keys(staged)]
        )
        for signal in chosen:
            dict.pop(staged, signal, None)
            self._staged_ns.pop(signal, None)
            self._requested.pop(signal, None)
            self._written_by.pop(signal, None)
        if (writer := self._writers.get(device)) is not None:
            if signals is None:
                writer.clear()
            else:
                writer.discard(signals)
        retry = None if signals is not None else self._retries.pop(device, None)
        if retry is not None and retry.timer is not None:
            retry.timer.cancel()

    def force(
        self, device: Device, values: Mapping[Signal, float]
    ) -> tuple[dict[str, float], tuple[Writer, int] | None]:
        """Write `values` as a stop does. Under the rig's lock, which the caller holds.

        Past every latch, hold, permissive and `max_rate`; each value finite and each
        signal writable. What was staged on the device is replaced (A6). A device that
        commits inline is committed now, and its written values returned by address; a
        blocking one's writer is handed the values, and `(writer, ticket)` returned to
        wait on.

        Raises:
            ConflictError: A signal is not writable.
            ValueError: A value is not finite.
            Exception: The commit raised (the values stay staged, as for any failed commit).
        """
        if not isinstance(device, Committable):
            raise ConflictError(f"'{device.name}' has nothing to commit: no demands")
        for signal, value in values.items():
            if Access.W not in signal.access:
                raise ConflictError(_not_writable(signal))
            if not math.isfinite(value):
                raise ValueError(f"stop of '{signal.address}' is not finite: {value!r}")
        self._drop_staged(device, None)
        time_ns = self.clock.now_ns()
        writer = self._writer_for(device)
        for signal, value in values.items():
            if writer is None:
                device.apply(signal, time_ns, value)
                self._staged_ns[signal] = time_ns
            else:
                writer.apply(signal, time_ns, value)
            self._written_by[signal] = "stop"
        if writer is not None:
            return {s.address: v for s, v in values.items()}, (writer, writer.request(time_ns))
        self._forcing.add(device)
        try:
            states = self._committing((device,), time_ns)
            if states and self.recorder is not None:
                self.recorder.record((), (), states, time_ns=time_ns)
        finally:
            self._forcing.discard(device)
            self._flush_pushed()
        self._deliver(states)
        return {
            s.address: (
                v if (state := states.get(s)) is None or state.value is None else state.value
            )
            for s, v in values.items()
        }, None

    def run_stop_command(self, device: Device, command: str) -> dict[str, float]:
        """Run `device`'s `stops=True` command as a stop does. Under the rig's lock (the caller's).

        Past the latch and any controller (they are in manual by now, or about to be);
        what was staged on the device is replaced first. Returns what its demands read
        after it, by address.
        """
        spec = device.commands[command]
        self._drop_staged(device, None)
        self._forcing.add(device)
        try:
            self._run_locked(device, spec, command, {}, {}, ())
        finally:
            self._forcing.discard(device)
        out: dict[str, float] = {}
        for signal in device.demands.values():
            reading = self.router.latest.get(signal)
            if reading is not None and isinstance(reading.value, (int, float)):
                out[signal.address] = float(reading.value)
        return out

    def running_commands(self) -> list[Device]:
        """The devices running a long command now (a dose, a move): a copy, safe off the lock."""
        return list(self._running)

    def _commit(
        self,
        devices: Iterable[Device],
        time_ns: int,
        failed: dict[Signal, WriteState] | None = None,
    ) -> dict[Signal, WriteState]:
        """One commit per device, and the states filled in with what the rig knows.

        A blocking device's commit is handed to its writer instead, and its
        states come back through `written` when the write completes.

        A commit that raises is that device's alone: its demands stay staged
        -- they go out with the next commit, a newer demand on a signal
        replacing its own -- a `write_failed` condition holds on the device
        until a commit succeeds, a retry is armed on the rig clock, and the
        other devices commit regardless. Into `failed`, when given, go the
        states of what it kept (`value` None: nothing was set), for the
        controllers driving them; without it -- a manual demand or a command,
        one device -- the error is raised to the caller too (the value stays
        staged and is retried all the same).
        """
        states: dict[Signal, WriteState] = {}
        latches = self.stopping.latches
        for device in devices:
            if not isinstance(device, Committable):
                continue  # touched by an input landing; nothing to commit
            if device not in self._forcing and latches.any():
                if latches.of_device(device):
                    self.replace_staged([device], [])  # a latched device commits nothing
                    continue
                if held := latches.signals_held(device):
                    self.replace_staged([], held)
            if (writer := self._writer_for(device)) is not None:
                writer.request(time_ns)
                continue
            before = dict(self.router.seq)
            sent = {s: v for s, v in dict.items(device.staged)}
            try:
                device.commit(time_ns)
            except Exception as error:
                kept = self._commit_failed(device, error, time_ns)
                if failed is None:
                    raise
                failed.update(kept)
                continue
            recovered = self.conditions.clear(device, Code.WRITE_FAILED, message="writes succeed")
            retry = self._retries.pop(device, None)
            if retry is not None and retry.timer is not None:
                retry.timer.cancel()
            staged_ns = {s: self._staged_ns.get(s) for s in sent}
            states.update(self._states(device, time_ns, before))
            if recovered is not None:
                self._writes_recovered(device)
            if retry is not None:
                self._resent(
                    device,
                    [(s, v, staged_ns[s]) for s, v in sent.items() if retry.kept.get(s) == v],
                )
        return states

    def _commit_failed(
        self, device: Committable, error: Exception, time_ns: int
    ) -> dict[Signal, WriteState]:
        """A commit raised: keep its demands staged, hold `write_failed`, arm a retry (A6).

        Each demand keeps its `requested` record and `at_limit` until a commit of it
        succeeds, a newer demand replaces it, or it is dropped for its age. Returns what
        each demand is now: nothing set (`value` None).
        """
        message = f"{type(error).__name__}: {error}"
        staged = list(dict.keys(device.staged))
        raised = self.conditions.set(  # raised once per outage, not once per delivery
            device,
            Code.WRITE_FAILED,
            Severity.ERROR,
            message,
            {"signals": [signal.address for signal in staged]},
        )
        if raised:
            log.warning("%s: commit failed: %s", device.name, message, exc_info=error)
        kept: dict[Signal, WriteState] = {}
        for signal in staged:
            holder = self.controllers.driving(signal)
            kept[signal] = WriteState(
                value=None,
                requested=self._requested.get(signal),
                controller=None if holder is None else holder.name,
            )
            self._staged_ns.setdefault(signal, time_ns)
        device.staged.forget_reads()  # the retry's commit reads them afresh
        self._writes_failing(device, staged)
        self._retry_later(device).kept.update(dict.items(device.staged))
        return kept

    def writes_failed(self, device: Device, signals: Iterable[Signal]) -> None:
        """A blocking device's writer failed a write of `signals`.

        Its echo demands go stale, and a retry is armed.
        """
        with self.lock:
            if self.devices.get(device.name) is device:
                self._writes_failing(device, signals)
                self._retry_later(device)

    def resent(self, device: Device, sent: Iterable[tuple[Signal, float, int | None]]) -> None:
        """A blocking device's writer committed values a failed write had kept: say so."""
        with self.lock:
            if self.devices.get(device.name) is device:
                retry = self._retries.pop(device, None)
                if retry is not None and retry.timer is not None:
                    retry.timer.cancel()
                self._resent(device, sent)

    def _resent(self, device: Device, sent: Iterable[tuple[Signal, float, int | None]]) -> None:
        """One `resent` event per value a failed write kept and a commit has now set."""
        for signal, value, staged_ns in sent:
            at = (
                ""
                if staged_ns is None
                else f", staged at {self.clock.from_start_s(staged_ns):.3f} s"
            )
            self.event(
                Severity.INFO,
                Scope.DEVICE,
                device.name,
                Code.RESENT,
                f"re-sent {signal.address}={value:g}{at}",
                {"signal": signal.address, "value": value, "staged_ns": staged_ns},
            )

    # region Write retries (A6)

    def retry_max_age_s(self, device: Device) -> float:
        """How long a value a failed write kept may wait to be sent: the device's key, or 60 s."""
        entry = self.entries.get(device.name)
        own = None if entry is None else entry.retry_max_age_s
        return RETRY_MAX_AGE_S if own is None else own

    def _retry_later(self, device: Device) -> _Retry:
        """Arm `device`'s next write retry, unless one is armed.

        The first after `min(poll_s, 5 s)`, then each after twice the last, up to 60 s.
        """
        retry = self._retries.get(device)
        if retry is None:
            first = min(poll_period(device) or RETRY_FIRST_S, RETRY_FIRST_S)
            retry = self._retries[device] = _Retry(first)
        elif retry.timer is not None and retry.timer.active:
            return retry  # a retry is due already: this failure came from other traffic
        else:
            retry.interval_s = min(retry.interval_s * 2, RETRY_MAX_S)
        retry.timer = self.after(
            retry.interval_s, lambda: self._retry_due(device, retry), f"retry {device.name}"
        )
        return retry

    def _retry_due(self, device: Device, retry: _Retry) -> None:
        """The retry came up: drop what is too old, then commit what is kept, if anything."""
        with self.lock:
            if (
                self._retries.get(device) is not retry
                or self.devices.get(device.name) is not device
            ):
                return
            if self.conditions.get(device, Code.WRITE_FAILED) is None:
                del self._retries[device]
                return
            now = self.clock.now_ns()
            cutoff = now - round(self.retry_max_age_s(device) * 1e9)
            writer = self._writers.get(device)
            if writer is not None:
                self._dropped(device, writer.drop_older_than(cutoff))
                if not writer.retry(now):
                    del self._retries[device]  # nothing left to send: the next demand will
                return
            assert isinstance(device, Committable)
            old = [
                (signal, value, at)
                for signal, value in dict.items(device.staged)
                if (at := self._staged_ns.get(signal, now)) < cutoff
            ]
            for signal, _, _ in old:
                dict.pop(device.staged, signal, None)
            self._dropped(device, old)
            if not dict.__len__(device.staged):
                del self._retries[device]
                return
            self._touched = {}
            failed: dict[Signal, WriteState] = {}
            try:
                states = self._commit((device,), now, failed)
            finally:
                self._touched = None
            self._deliver(failed)
            self._deliver(states)
            if states and self.recorder is not None:
                self.recorder.record((), (), states, time_ns=now)
            self._flush_pushed()

    def _dropped(self, device: Device, old: Iterable[tuple[Signal, float, int]]) -> None:
        """Values kept too long (`retry_max_age_s`): not sent.

        Each stays `stale(write_failed)` until a new demand of it commits.
        """
        retry = self._retries.get(device)
        for signal, value, staged_ns in old:
            if retry is not None:
                retry.kept.pop(signal, None)
            self._requested.pop(signal, None)
            self._staged_ns.pop(signal, None)
            signal.at_limit = None
            self._write_lost.setdefault(device, set()).add(signal)
            age_s = (self.clock.now_ns() - staged_ns) / 1e9
            self.event(
                Severity.WARNING,
                Scope.DEVICE,
                device.name,
                Code.WRITE_DROPPED,
                f"dropped {signal.address}={value:g}: not sent in {age_s:.0f} s, past"
                f" retry_max_age_s {self.retry_max_age_s(device):g}",
                {"signal": signal.address, "value": value, "age_s": age_s},
            )

    # endregion

    def writes_recovered(self, device: Device) -> None:
        """A blocking device's writer wrote again after failing: its echo demands are known."""
        with self.lock:
            if self.devices.get(device.name) is device:
                retry = self._retries.pop(device, None)
                if retry is not None and retry.timer is not None:
                    retry.timer.cancel()
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
        """What a poll of `device` delivered: its read path, for `stale(device_offline)`.

        Also the base of its signals' `pending` deadline.
        """
        path = self._read_path.setdefault(device, set())
        delivered = False
        for sample in samples:
            path.update(sample.values)
            delivered = True
        self.liveness.device_read(device, self.clock.now_ns(), delivered)

    def device_offline(self, device: Device) -> None:
        """`device` went offline: what its reads delivered is `stale(device_offline)` at once.

        Its readouts and sensed demands on its read path (what its polled
        reads have delivered) get a `stale(device_offline)` reading; its
        settings, configs and echo demands keep theirs. A signal never read
        stays `pending` until its deadline. Each returns to `ok` with its next read.
        """
        self._device_down(device, Reason.DEVICE_OFFLINE)

    def device_hung(self, device: Device) -> None:
        """`device`'s poll is stuck in a read: its read path is `stale(device_hung)` at once."""
        self._device_down(device, Reason.DEVICE_HUNG)

    def _device_down(self, device: Device, reason: Reason) -> None:
        with self.lock:
            if self.devices.get(device.name) is not device:
                return
            gone = stale(reason)
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
            self._staged_ns.pop(signal, None)
            writer = self._written_by.pop(signal, None)
            before_value = self.router.latest.get(signal)
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
            if isinstance(device, Values):
                was = (
                    None if before_value is None or not before_value.usable else before_value.value
                )
                self.values.written(signal, value, was, writer)
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
                self.bind_inputs(device, entry.inputs)
            except Exception:
                self._drop_device(device)
                raise
            self.entries[name] = entry
            if start:
                self.start_polling(device)
            if self.recorder is not None:
                self.recorder.declare(s for s in device.signals.values() if s.spec.record)
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
        # Its own inputs, and every binding that follows it (another device's, a step's):
        # unbound, so each reads `pending` rather than a device that is gone.
        followers = [
            binding
            for table in (self._followers, self._node_followers)
            for source, bindings in table.items()
            for binding in bindings
            if source.device is device or binding.owner is device
        ]
        for binding in followers:
            self.unbind(binding)
            owner = binding.owner
            if owner is not device and isinstance(owner, Device) and binding.declared is None:
                owner.bound.pop(binding.name, None)  # a name only the rig file gave
        self._write_lost.pop(device, None)
        self._read_path.pop(device, None)
        self.liveness.unwatch(device)
        if (retry := self._retries.pop(device, None)) is not None and retry.timer is not None:
            retry.timer.cancel()
        for signal in device.signals.values():
            self._staged_ns.pop(signal, None)
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
            self.values.forget(signal)
            self._written_by.pop(signal, None)
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
                    setpoint_period_s=c.setpoint_period_s,
                    on_fault=c.on_fault.document(),  # type: ignore[arg-type]  validated as the file is
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
        self,
        device: Device,
        command: str,
        args: Mapping[str, Any] | None = None,
        *,
        actor: Actor | None = None,
    ) -> Any:
        """Run `device`'s `command` with `args`: what the method returned.

        [invoke][flyball.rig.rig.Rig.invoke] without the controllers it put into manual.
        """
        return self.invoke(device, command, args, actor=actor).result

    def invoke(
        self,
        device: Device,
        command: str,
        args: Mapping[str, Any] | None = None,
        *,
        actor: Actor | None = None,
    ) -> CommandRun:
        """Run `device`'s `command` with `args`, as the rig: linked, clamped, owned, recorded.

        An argument that is a value for a demand (`Annotated[..., d]`) is filled from
        that demand's current value when left out, and clamped to the
        signal's effective limits. A synthesised `set_<name>` goes through
        [demand][flyball.rig.rig.Rig.write]. A command that changes
        what drives the device -- one with a `mode`, a linked demand, or
        `writes=` -- is refused while a controller drives one of the device's
        demands, unless it `interrupts`: the refusal is checked before the
        method runs, and each such controller is put into manual (with an
        event) only once the method has succeeded, so a command that raises
        leaves them regulating. The method runs under the rig lock -- unless it is
        `long` (a dose, a move): then only the checks do, the method runs off
        the lock, so polling, deliveries and the device's `stop` carry on, and
        the rig re-enters the lock after it. Afterwards the
        device's `mode` output (if it has one) becomes the command's, a
        `commit=True` command commits the device, each linked demand the
        driver did not push gets its argument as its reading, and
        `last.<command>` records what ran. Returns what the method returned
        and the controllers it put into manual.

        A latch on the device refuses a command that drives it as it refuses a
        write ([write][flyball.rig.rig.Rig.write]): a person's (`actor.person`)
        goes through the rig stop, logged; an automatic one does not. The
        device's own stop command (`stops=True`), a simulation's command and a
        command that drives nothing (a setting) are never refused by a latch.

        Raises:
            NotFoundError: No such command.
            ConflictError: A latch holds the device; a controller drives the
                device; or a long command while the device runs another, or
                while the caller holds the rig lock (it would wait under it).
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
            return CommandRun(self.write(signal.node, {signal: given["value"]}, actor=actor))
        forced = self._latched_command(device, spec, actor)
        if spec.long and _held(self.lock):
            raise ConflictError(
                f"{device.name}.{command} waits: it cannot run while the rig lock is held"
            )
        with self.lock:
            linked, displaced = self._command_checks(device, spec, command, given)
            if not spec.long:
                if forced:
                    self._forcing.add(device)
                try:
                    return self._run_locked(device, spec, command, given, linked, displaced)
                finally:
                    self._forcing.discard(device)
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
                return CommandRun(result)  # removed while it ran: nothing of it is the rig's
            interrupted = self._displace(displaced, device, command)
            self._touched = {}
            try:
                self._command_ran(
                    device, spec, command, given, linked, before, self.clock.now_ns(), outer=None
                )
            finally:
                self._touched = None
                self._flush_pushed()
        return CommandRun(result, interrupted)

    def _latched_command(self, device: Device, spec: CommandSpec, actor: Actor | None) -> bool:
        """Whether a latch refuses `spec` on `device` (raised), or it goes through the rig stop.

        Returns True for a person's command through the rig stop (logged, forced).
        """
        if spec.stops or spec.simulation or not self.stopping.latches.any():
            return False
        drives = (
            spec.mode is not None
            or bool(spec.writes)
            or any(
                p.link is not None and device.signals[p.link].role is Role.DEMAND
                for p in spec.params.values()
            )
        )
        if not drives:
            return False
        person = actor is not None and actor.person
        through = None
        for signal in device.demands.values():
            refused, passed = self.stopping.refusal(signal, by=None, person=person)
            if refused is not None:
                raise ConflictError(f"{device.name}.{spec.name}: {refused}")
            through = through or passed
        if through is None:
            return False
        assert actor is not None
        self.event(
            Severity.WARNING,
            Scope.DEVICE,
            device.name,
            Code.WRITTEN_WHILE_STOPPED,
            f"{spec.name} run by {actor.sub} while the rig is stopped; still stopped",
            {"command": spec.name, "by": actor.sub},
        )
        return True

    def _command_checks(
        self, device: Device, spec: CommandSpec, command: str, given: dict[str, Any]
    ) -> tuple[dict[str, Signal], list[Controller]]:
        """Fill and clamp the linked arguments; refuse, or list to displace, a driving controller.

        Under the lock, before the method runs. Returns the linked arguments'
        signals, by argument, and the controllers an `interrupts` command puts
        into manual once it has succeeded.
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
        drives = (
            spec.mode is not None
            or bool(spec.writes)
            or any(s.role is Role.DEMAND for s in linked.values())
        )
        displaced: list[Controller] = []
        if drives:
            # It changes what drives the device: not while a controller does.
            # A setting (a blend flow) is not what a controller drives.
            for signal in device.signals.values():
                holder = self.controllers.driving(signal)
                if holder is None or not holder.mode.active() or holder in displaced:
                    continue
                if not spec.interrupts:
                    raise ConflictError(
                        f"'{signal.address}' is driven by controller {holder.name!r}:"
                        f" {command!r} would fight it; put it in manual, or detach it"
                    )
                displaced.append(holder)
        return linked, displaced

    def _displace(
        self, displaced: Sequence[Controller], device: Device, command: str
    ) -> tuple[Interrupted, ...]:
        """Under the lock, once the command has succeeded: each displaced controller to manual."""
        interrupted: list[Interrupted] = []
        for holder in displaced:
            if not holder.mode.active():
                continue  # put in manual meanwhile (a long command's wait)
            was = holder.mode.value
            holder.manual()
            interrupted.append(Interrupted(holder.name, was))
            self.event(
                Severity.INFO,
                Scope.CONTROLLER,
                holder.name,
                Code.INTERRUPTED,
                f"put in manual by {device.name}.{command}",
                {"was": was, "by": f"{device.name}.{command}"},
            )
            if self.controller_states.watched:
                self.controller_states.set(holder.name, holder.state)
        return tuple(interrupted)

    def _run_locked(
        self,
        device: Device,
        spec: CommandSpec,
        command: str,
        given: dict[str, Any],
        linked: Mapping[str, Signal],
        displaced: Sequence[Controller],
    ) -> CommandRun:
        """A command that does not wait: the method and what follows it, under the lock."""
        outer = self._touched
        if outer is None:
            self._touched = {}
        time_ns = self.clock.now_ns()
        before = dict(self.router.seq)
        try:
            result = spec.method(device, **given)
            interrupted = self._displace(displaced, device, command)
            self._command_ran(device, spec, command, given, linked, before, time_ns, outer=outer)
        finally:
            if outer is None:
                self._touched = None
                self._flush_pushed()  # a failed commit's stale demands are delivered too
        return CommandRun(result, interrupted)

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
        setpoint_period_s: float | None = None,
        on_fault: OnFault | None = None,
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
            setpoint_period_s: Re-apply a moving setpoint's feedforward this often between
                readings; default `max(0.1 s, poll_s / 4)` from `measured`'s `poll_s`.
            on_fault: What it does once its source's outage is released; default `freeze`.

        Raises:
            SignalClaimedError: `output` is already driven, or `measured`
                already regulated, by another controller.
            ConflictError: `output` is not writable, `measured` not published,
                the feedforward cannot map the units, or `on_fault: stop` on an
                output whose stop is `keep` (it would do nothing, and look as if
                it did).
        """
        from flyball.model.controller import FaultAction

        if (
            on_fault is not None
            and on_fault.action is FaultAction.STOP
            and type(output.device).stop_command is None
            and resolve_output(output).value is None
        ):
            raise ConflictError(
                f"controller {output.address!r}: on_fault stop would do nothing -- its output's"
                " stop is keep; give the output a `stop:` value, or use stop_device or manual"
            )
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
                setpoint_period_s=setpoint_period_s,
                write=write,
                hold=lambda: self.hold_reason(controller),
                on_fault=on_fault,
            )
            controller.on_reference = lambda: self._reference_changed(controller)
            controller.guard = lambda: self.stopping.regulate_refusal(controller)
            controller.on_reseed = lambda was, now: self._reseeded(controller, was, now)
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
            self.faults.forget(controller)
            if (timer := self._reapplying.pop(controller, None)) is not None:
                timer.cancel()
            self.conditions.clear_owner(controller, reason="detached")
            controller.on_reference = Controller._nothing
            controller.guard = Controller._never_refused
            controller.on_reseed = Controller._reseeded
            controller.manual()
            controller.write = Controller._unwired
            controller.hold = Controller._never_held
            # A watcher primes from this cell; a name the rig no longer has must not be in it.
            self.controller_states.discard(name)
            self._changed(f"detached controller {name}")
            return controller

    def _reseeded(self, controller: Controller, was: float | None, now: float | None) -> None:
        """A controller resuming after a hold re-seeded its trajectory from the reading: say so."""
        later = "" if was is None or now is None or now <= was else f", {now - was:.3f} s later"
        self.event(
            Severity.INFO,
            Scope.CONTROLLER,
            controller.name,
            Code.RESEEDED,
            f"resumed after a hold: its trajectory goes on from the reading at its own rate{later}",
            {"end_was_s": was, "end_s": now},
        )

    def setpoint_period_s(self, controller: Controller) -> float:
        """How often `controller` re-applies a moving setpoint's feedforward between readings.

        Its own `setpoint_period_s`, else `max(0.1 s, poll_s / 4)` from its measured
        signal's `poll_s` (1 s for a push).
        """
        if controller.setpoint_period_s is not None:
            return controller.setpoint_period_s
        poll_s = controller.measured_signal.poll_s or 1.0
        return max(SETPOINT_PERIOD_MIN_S, poll_s / 4)

    def _reference_changed(self, controller: Controller) -> None:
        """A controller's reference or mode changed: arm or cancel its re-apply (E25).

        And look at its source's outage again: a release waits for REGULATING.

        Off a moving setpoint -- MANUAL above all, which a stop puts every controller in --
        nothing takes the rig's lock: the re-apply is cancelled, and would find nothing to do.
        """
        now = self.clock.now_ns()
        if not controller.follows(now):
            if (timer := self._reapplying.pop(controller, None)) is not None:
                timer.cancel()
            if controller.mode.active():
                with self.lock:
                    self.faults.regulating(controller)
            return
        with self.lock:
            if self.controllers.find(controller.measured_signal) is not controller:
                return  # detached
            self.faults.regulating(controller)
            if (timer := self._reapplying.get(controller)) is not None and timer.active:
                return  # already following; the new generator is read at each re-apply
            timer = self.every(
                self.setpoint_period_s(controller),
                lambda: self._reapply(controller),
                f"reapply {controller.name}",
            )
            if timer is not None:
                self._reapplying[controller] = timer

    def _reapply(self, controller: Controller) -> None:
        """The re-apply came up: feedforward of the setpoint now plus the last correction.

        Serialised like a delivery: under the lock, in its own `_touched`, then one commit,
        the controller's state and a tick recorded with no reading. Once the setpoint stops
        moving (the generator finished, MANUAL, detached), it cancels itself.
        """
        with self.lock:
            timer = self._reapplying.get(controller)
            now = self.clock.now_ns()
            if timer is None or not controller.follows(now):
                if timer is not None:
                    timer.cancel()
                    del self._reapplying[controller]
                return
            if self._touched is not None:
                return  # inside a delivery on this thread (a stepped clock): next time
            touched: dict[Device, None] = {}
            self._touched = touched
            failed: dict[Signal, WriteState] = {}
            try:
                if not controller.reapply(now):
                    return
                states = self._commit(touched, now, failed)
            finally:
                self._touched = None
            self._deliver(failed)
            self._deliver(states)
            if self.controller_states.watched:
                self.controller_states.set(controller.name, controller.state)
            if self.recorder is not None:
                self.recorder.record((), [(controller, None)], states, time_ns=now)
            self._flush_pushed()

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
                received = self.clock.now_ns()
                for sample in samples:
                    self.router.note(sample, received)
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
        ticks: list[tuple[Controller, Reading | None]] = []
        published: list[Sample] = []
        touched: dict[Device, None] = {}
        landed: dict[InputBinding, None] = {}
        received = self.clock.now_ns()
        watches = self.liveness.watches
        self._touched = touched
        try:
            for sample in samples:
                if not noted:
                    self.router.note(sample, received)
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
                messages: dict[tuple[InputBinding, Node], None] = {}
                for reading in sample.readings(received):
                    signal = reading.signal
                    if signal in watches:
                        self.liveness.arrived(reading, received)
                    self.bands.check(reading)  # noted: its band condition, raised or cleared
                    for binding in self._followers.get(signal, ()):
                        landed[binding] = None  # its holder reads the router
                    # Every node on the way up from the signal, not just
                    # the sample's, is an instant on that node: a binding
                    # to it hears it.
                    node: Node | None = signal.node
                    while node is not None:
                        for binding in self._node_followers.get(node, ()):
                            messages[binding, node] = None
                        node = node.parent
                    if (controller := self.controllers.find(signal)) is not None and (
                        self._stepped is None or controller not in self._stepped
                    ):
                        ticks.append((controller, reading))
                for binding, node in messages:
                    # A subscriber hears what publishes, as a signal-level
                    # binding requires P; a fresh read of an R-only setting
                    # is for whoever asked for it.
                    if (message := sample.under(node)) is not None and (
                        message.published()
                    ) is not None:
                        landed[binding] = None
            time_ns = max(s.time_ns for s in samples)
            if landed:
                self._landed(landed, touched, time_ns)
            for controller, reading in ticks:
                if self._stepped is not None:
                    self._stepped.add(controller)
                assert reading is not None
                self._step(controller, reading)
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

    def _landed(
        self, landed: Iterable[InputBinding], touched: dict[Device, None], time_ns: int
    ) -> None:
        """Bindings whose source got a reading in this delivery: tell their watchers and holders.

        A device holding one is told through `inputs_changed` now, before the
        controllers step, and committed with the rest at the end: what it
        pushes is delivered next in the same chain.
        """
        changed: dict[Device, list[InputBinding]] = {}
        for binding in landed:
            binding.notify()
            if isinstance(owner := binding.owner, Device):
                touched[owner] = None  # it reads the router in `commit`
                changed.setdefault(owner, []).append(binding)
        for device, bindings in changed.items():
            self._inputs_changed(device, time_ns, bindings)

    def _step(self, controller: Controller, reading: Reading) -> None:
        """One controller's step, kept from the rest of the delivery.

        A law that raises (no law set, a NaN it cannot take) would otherwise
        abort the whole delivery: every other controller's step, the commits,
        the readings, the recorder and the stream. It becomes an event
        instead, and a fault released at once: its `on_fault` action, at
        least `manual` (latched until a person resets it).
        """
        self._fresh[controller] = self._fresh.get(controller, 0) + 1 if reading.usable else 0
        self.faults.delivered(controller, reading)  # A5: fault time starts, pauses, or ends
        try:
            controller.on_reading(reading)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            raised = self.conditions.set(
                controller,
                Code.STEP_FAILED,
                Severity.ERROR,
                message,
                {"measured": reading.signal.address},
            )
            if raised:  # the traceback once per outage, not once per step
                log.exception("controller %s failed its step", controller.name)
            self.faults.law_failed(controller, message)
            return
        if controller.mode.active():  # in manual (a law error's latch) it did not step
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
