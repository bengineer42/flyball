"""The software stop: who asked, what each output's stop is, what a stop did, and the latch.

Here rather than in `flyball.runner.stopping` so the server's stop route can name
them: the server sits below the runner and may not import it. `flyball.runner.stopping`
re-exports them beside the break-glass.

**What a stop writes.** Each device has a *resolved stop*:

- a device whose driver marks a command `stops=True` (a blender's pumps off, a DAC's
  power-down) is stopped by that command, and a rig file's `stop:` values are
  refused on it;
- otherwise each writable demand takes the first of: the rig file's `stop:` value (a
  number, or `keep`), the driver's declared `off` (the output's inactive level),
  else `keep` -- left as it is, energised if it was.

A declared `off` is written even outside the signal's limits; nothing a stop writes
goes through `max_rate`, a hold, a permissive or a latch. Stop is a control function,
not a safety function: "stopped" means flyball refuses its own automatic writes, not
that the outputs are off.

**A stop** ([RigStopper][flyball.rig.stopping.RigStopper]): the rig's latch first, so
nothing automatic writes from then on; every running long command cancelled
(`Device.cancel`); the program interrupted; every controller to manual; then every
device stopped at once, each within `DEVICE_STOP_S`, never waiting behind a stuck
delivery or command for longer. Each device is reported `stopped`, `unchanged` (it
kept every output) or `failed`. The latch holds until a person resets it; a restart
re-applies it.

**`on_fault`** acts here too: a controller whose source's outage is released goes to
manual, and `stop` / `stop_device` write its output's or device's resolved stop,
each latched until its own Reset.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from collections.abc import Iterable, Mapping
from threading import Lock
from typing import TYPE_CHECKING, Any, Literal, NotRequired, Protocol, TypedDict

from flyball.foundation.device import (
    KEEP,
    Access,
    Code,
    Device,
    Role,
    Scope,
    Severity,
    Signal,
)
from flyball.foundation.errors import NotFoundError
from flyball.model.controller import Controller, ControllerMode, FaultAction

from .faults import LAW_ERROR, Outage
from .latches import RIG_STOP, Latch, Latches, fault_cause, stoppable, subjects

if TYPE_CHECKING:
    from flyball.record.store import Store

    from .rig import Rig

__all__ = [
    "DEVICE_STOP_S",
    "Actor",
    "DeviceStop",
    "InterimStopper",
    "OutputStop",
    "Program",
    "RigStopper",
    "StopReport",
    "Stopper",
    "Stopping",
    "resolve_output",
]

log = logging.getLogger("flyball.stop")

DEVICE_STOP_S = 5.0
"""How long a stop waits for all devices together, a stuck lock or bus included: a device
not done by then is reported `failed` ("may still act"), and the stop returns."""

type Source = Literal["off", "you said", "nobody said"]


@dataclasses.dataclass(frozen=True)
class Actor:
    """Who asked for a stop: the principal's `sub`/`sid`/`kind`, and the way it came in."""

    sub: str
    sid: str
    kind: str
    via: Literal["http", "mcp", "signal"]
    detail: str = ""
    """Anything more about the caller, e.g. `signal from pid 4121 uid 1000`."""

    @property
    def person(self) -> bool:
        """A person at a UI or the HTTP API: a human principal, not an agent through MCP."""
        return self.kind == "human" and self.via != "mcp"


class DeviceStop(TypedDict):
    """What a stop did to one device."""

    state: Literal["stopped", "unchanged", "failed"]
    detail: str
    written: NotRequired[dict[str, float]]
    """What it wrote, by address: the value committed (a driver's readback where it gave one)."""
    kept: NotRequired[dict[str, float | None]]
    """What it left as it was, by address, with the value it holds (None: not known)."""


@dataclasses.dataclass(frozen=True)
class StopReport:
    """What one stop did, device by device."""

    at_ns: int
    """Wall-clock time the stop began, ns since the epoch (not the rig's clock)."""
    actor: Actor
    reason: str
    devices: dict[str, DeviceStop]
    """Every device with a writable signal and a demand, by name; one with none has nothing to
    stop (a `driver: values` device, whose writable settings a stop leaves as they are)."""
    program_interrupted: bool
    """Whether this stop found a program running and interrupted it."""
    controllers_manual: list[str]
    """Every controller in manual once the stop was done, by name."""
    interim: bool
    """True for the interim stop, which wrote nothing; False for the real one."""
    latched: bool = False
    """Whether the rig is latched stopped now (a stop through a stop route always is)."""

    def as_dict(self) -> dict[str, Any]:
        """The report as JSON-ready builtins: the route's body and the break-glass's log line."""
        return dataclasses.asdict(self)


class Stopper(Protocol):
    """Stops the rig. Thread-safe; one device failing is reported, never raised."""

    def stop(self, actor: Actor, reason: str) -> StopReport: ...


class _Running(Protocol):
    @property
    def running(self) -> bool: ...


class Program(Protocol):
    """What a stop needs of `flyball.sequencing.Programmer`, without importing it."""

    @property
    def state(self) -> _Running: ...
    def interrupt(self, reason: str) -> bool: ...


# region Resolution


@dataclasses.dataclass(frozen=True)
class OutputStop:
    """One writable demand's resolved stop: what a stop writes to it, and who said so."""

    signal: Signal
    value: float | None
    """What a stop writes; None: `keep` (left as it is)."""
    source: Source
    """`off` (the driver's inactive level), `you said` (the rig file's `stop:`), `nobody said`."""


def outputs(device: Device) -> list[Signal]:
    """`device`'s writable demands: what a stop may write."""
    return [s for s in device.signals.values() if s.role is Role.DEMAND and Access.W in s.access]


def resolve_output(signal: Signal) -> OutputStop:
    """`signal`'s stop: the rig file's `stop:`, else the driver's `off`, else keep."""
    if signal.stop == KEEP:
        return OutputStop(signal, None, "you said")
    if signal.stop is not None:
        return OutputStop(signal, float(signal.stop), "you said")
    if signal.spec.off is not None:
        return OutputStop(signal, signal.spec.off, "off")
    return OutputStop(signal, None, "nobody said")


# endregion


class InterimStopper:
    """The stop before the real one: interrupt, every controller to manual, write nothing.

    Kept for what still names it; the server's stop route uses
    [RigStopper][flyball.rig.stopping.RigStopper]. Every writable device is reported
    `unchanged`, never `stopped`.
    """

    rig: Rig
    program: Program | None

    def __init__(self, rig: Rig, program: Program | None = None) -> None:
        self.rig = rig
        self.program = program
        self._lock = Lock()

    def stop(self, actor: Actor, reason: str) -> StopReport:
        at_ns = time.time_ns()
        with self._lock:
            interrupted = _interrupt(self.program, "the rig was stopped")
            failed = _manual(self.rig, None)
        devices: dict[str, DeviceStop] = {}
        for name, device in list(self.rig.devices.items()):
            if not stoppable(device):
                continue
            errors = [failed[s.address] for s in outputs(device) if s.address in failed]
            devices[name] = (
                {"state": "failed", "detail": "; ".join(errors)}
                if errors
                else {
                    "state": "unchanged",
                    "detail": "interim stop: controllers to manual; nothing written",
                }
            )
        return StopReport(
            at_ns=at_ns,
            actor=actor,
            reason=reason,
            devices=devices,
            program_interrupted=interrupted,
            controllers_manual=_in_manual(self.rig),
            interim=True,
        )


class Stopping:
    """The rig's stops and latches: what refuses a write, what a stop writes, Reset, `on_fault`.

    One per rig (`rig.stopping`). The write path asks it
    ([refusal][flyball.rig.stopping.Stopping.refusal]) before anything is applied; the
    fault timer's release calls [on_fault][flyball.rig.stopping.Stopping.on_fault].
    """

    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self.latches = Latches(rig)

    # region Refusals

    def refusal(
        self, signal: Signal, *, by: Controller | None, person: bool
    ) -> tuple[str | None, Latch | None]:
        """Why a write to `signal` is refused now, and the rig stop it goes through, if any.

        A fault latch on the signal or its device refuses everyone. The rig stop refuses
        automatic writers (a controller, a program, an agent) and lets a person's through,
        forced and logged, returning the latch it passed.
        """
        held = self.latches.of_signal(signal)
        if not held:
            return None, None
        for latch in held:
            if latch.cause != RIG_STOP:
                return f"'{signal.address}' is {latch.said()}: reset it to write", None
        stop = held[0]
        if by is None and person:
            return None, stop
        return f"the rig is {stop.said()}: reset it to write", None

    def regulate_refusal(self, controller: Controller) -> str | None:
        """Why `controller` may not regulate now: a latch on it, its output or the rig."""
        held = self.latches.of_controller(controller)
        if not held:
            return None
        return f"controller {controller.name!r}: {held[0].said()}; reset it first"

    # endregion

    # region Latching

    def latch(self, latch: Latch) -> bool:
        """Hold `latch`: conditions raised, staged values on what it holds replaced (A6)."""
        if not self.latches.set(latch):
            return False
        rig = self.rig
        details = {
            "cause": latch.cause,
            "action": latch.action,
            "by": latch.by,
            "at_ns": latch.at_ns,
            "reason": latch.reason,
        }
        if latch.cause == RIG_STOP:
            rig.conditions.set(
                rig,
                Code.STOPPED,
                Severity.WARNING,
                f"stopped by {latch.by}" + (f": {latch.reason}" if latch.reason else ""),
                {"by": latch.by, "at_ns": latch.at_ns, "reason": latch.reason},
            )
        else:
            for owner in self._owners(latch):
                rig.conditions.set(
                    owner, Code.LATCHED, Severity.ERROR, f"latched: {latch.said()}", details
                )
        rig.replace_staged(self._held_devices(latch), self._held_signals(latch))
        return True

    def reset(self, cause: str, actor: Actor) -> Latch:
        """A person's Reset of `cause`: the latch lets go; nothing resumes.

        A fault's Reset clears its controller's law (`clear_law`), so what made the law
        raise (a NaN in its state) does not persist into the next `regulate`.

        Raises:
            NotFoundError: No latch holds for `cause`.
        """
        rig = self.rig
        latch = self.latches.clear(cause)
        if latch is None:
            raise NotFoundError(f"No latch holds for {cause!r}")
        message = f"reset by {actor.sub}: {cause}"
        if latch.cause == RIG_STOP:
            rig.conditions.clear(rig, Code.STOPPED, message=message)
        else:
            for owner in self._owners(latch):
                rig.conditions.clear(owner, Code.LATCHED, message=message)
            name = cause.removeprefix("on_fault:")
            if (controller := rig.controllers.get(name)) is not None:
                controller.clear_law()
        rig.event(
            Severity.INFO,
            Scope.RIG,
            rig.name or "rig",
            Code.RESET,
            message,
            {"cause": cause, "by": actor.sub, "latch": latch.as_dict()},
        )
        return latch

    def _owners(self, latch: Latch) -> list[object]:
        """The live objects a latch's subjects name, for their conditions."""
        rig = self.rig
        found: list[object] = []
        for subject in latch.subjects:
            if subject.scope == "controller":
                owner: object | None = rig.controllers.get(subject.subject)
            elif subject.scope == "device":
                owner = rig.devices.get(subject.subject)
            elif subject.scope == "signal":
                try:
                    owner = rig.resolve(subject.subject)
                except NotFoundError:
                    owner = None
            else:
                owner = None
            if owner is not None:
                found.append(owner)
        return found

    def _held_devices(self, latch: Latch) -> list[Device]:
        rig = self.rig
        if latch.cause == RIG_STOP:
            return [d for d in list(rig.devices.values()) if stoppable(d)]
        return [
            device
            for s in latch.subjects
            if s.scope == "device" and (device := rig.devices.get(s.subject)) is not None
        ]

    def _held_signals(self, latch: Latch) -> list[Signal]:
        found: list[Signal] = []
        for subject in latch.subjects:
            if subject.scope == "signal":
                try:
                    signal = self.rig.resolve(subject.subject)
                except NotFoundError:
                    continue
                if isinstance(signal, Signal):
                    found.append(signal)
        return found

    # endregion

    # region on_fault

    def on_fault(self, outage: Outage) -> None:
        """A controller's outage was released: run its `on_fault` action. Under the rig's lock.

        `freeze` does nothing more; a law error takes at least `manual`. `stop` writes
        the output signal's resolved stop (its device's stop, where a command stops it);
        `stop_device` the output's device's. Each latches until its own Reset.
        """
        controller = outage.controller
        law_error = outage.reason.startswith(LAW_ERROR)
        action = controller.on_fault.escalated() if law_error else controller.on_fault.action
        if action is FaultAction.FREEZE:
            return
        rig = self.rig
        target = controller.output_signal
        device = target.device
        was = controller.mode.value
        controller.manual()
        held: list[tuple[Any, str]] = [("controller", controller.name)]
        if action is FaultAction.STOP and type(device).stop_command is None:
            held.append(("signal", target.address))
        elif action in (FaultAction.STOP, FaultAction.STOP_DEVICE):
            held.append(("device", device.name))
        latch = Latch(
            cause=fault_cause(controller.name),
            subjects=subjects(held),
            by="on_fault",
            at_ns=time.time_ns(),
            reason=outage.reason,
            action=action.value,
        )
        self.latch(latch)
        stop: DeviceStop | None = None
        if action is FaultAction.STOP and type(device).stop_command is None:
            stop = self.stop_locked(device, only=(target,))
        elif action in (FaultAction.STOP, FaultAction.STOP_DEVICE):
            stop = self.stop_locked(device)
        accrued_s = outage.accrual.total_ns(rig.clock.now_ns()) / 1e9
        rig.event(
            Severity.ERROR,
            Scope.CONTROLLER,
            controller.name,
            Code.ON_FAULT,
            f"source faulty for {accrued_s:.3f} s ({outage.reason}): {action.value}"
            + (" (a law error takes at least manual)" if law_error else ""),
            {
                "action": action.value,
                "reason": outage.reason,
                "accrued_s": accrued_s,
                "was": was,
                "stop": stop,
            },
        )

    # endregion

    # region Writing a stop

    def stop_locked(self, device: Device, only: Iterable[Signal] | None = None) -> DeviceStop:
        """Stop `device` (or only the outputs `only`) now, under the rig's lock held by the caller.

        For `on_fault` on the fault timer: nothing is waited for, so a blocking device's
        writer is handed the stop and reported as sent.
        """
        return self._stop(device, only, deadline=None)

    def stop_device(self, device: Device, deadline: float) -> DeviceStop:
        """Stop `device`, taking the rig's lock and waiting for the write until `deadline`.

        `deadline` is a `time.monotonic()` instant. Refused as `failed` if the lock is not
        free by then (a stuck delivery or command); the write of a blocking device is
        waited for on its writer, off the lock.
        """
        return self._stop(device, None, deadline=deadline)

    def _stop(
        self, device: Device, only: Iterable[Signal] | None, deadline: float | None
    ) -> DeviceStop:
        rig = self.rig
        chosen = None if only is None else set(only)
        plan = [resolve_output(s) for s in outputs(device) if chosen is None or s in chosen]
        command = type(device).stop_command if chosen is None else None
        values = {o.signal: o.value for o in plan if o.value is not None}
        kept = [o.signal for o in plan if o.value is None]
        locked = deadline is None
        if not locked:
            assert deadline is not None
            if not rig.lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
                return {
                    "state": "failed",
                    "detail": "the rig's lock was held throughout the stop's time (a delivery"
                    " or a command stuck): nothing written",
                    "written": {},
                    "kept": _values(rig, kept),
                }
        ticket = None
        try:
            if command is not None:
                try:
                    written = rig.run_stop_command(device, command)
                except Exception as error:
                    return self._fallback(device, command, error, plan, locked)
                return {
                    "state": "stopped",
                    "detail": f"ran its stop command {command!r}",
                    "written": written,
                    "kept": {},
                }
            if not values:
                rig.replace_staged([device], [])
                return {
                    "state": "unchanged",
                    "detail": _kept_detail(plan),
                    "written": {},
                    "kept": _values(rig, kept),
                }
            try:
                written, ticket = rig.force(device, values)
            except Exception as error:
                return {
                    "state": "failed",
                    "detail": f"writing its stop failed: {type(error).__name__}: {error}",
                    "written": {},
                    "kept": _values(rig, [*kept, *values]),
                }
        finally:
            if not locked:
                rig.lock.release()
        report: DeviceStop = {
            "state": "stopped",
            "detail": _written_detail(values, plan),
            "written": written,
            "kept": _values(rig, kept),
        }
        if ticket is not None:
            writer, number = ticket
            if deadline is None:
                report["detail"] += " (handed to its writer; not waited for)"
            elif not writer.wait(number, max(0.0, deadline - time.monotonic())):
                report["state"] = "failed"
                report["detail"] = "its writer did not answer in time: may still act"
            elif writer.failed is not None:
                report["state"] = "failed"
                report["detail"] = f"writing its stop failed: {writer.failed.message}"
        return report

    def _fallback(
        self, device: Device, command: str, error: Exception, plan: list[OutputStop], locked: bool
    ) -> DeviceStop:
        """The stop command raised: write each output's driver `off`, where one is declared."""
        rig = self.rig
        why = f"its stop command {command!r} failed: {type(error).__name__}: {error}"
        log.warning("%s: %s", device.name, why)
        values = {o.signal: o.value for o in plan if o.value is not None and o.source == "off"}
        if not values:
            return {
                "state": "failed",
                "detail": f"{why}; fallback: none effective",
                "written": {},
                "kept": _values(rig, [o.signal for o in plan]),
            }
        try:
            written, _ = rig.force(device, values)
        except Exception as second:
            return {
                "state": "failed",
                "detail": f"{why}; fallback failed too: {type(second).__name__}: {second}",
                "written": {},
                "kept": _values(rig, [o.signal for o in plan]),
            }
        return {
            "state": "failed",
            "detail": f"{why}; fallback wrote each declared off",
            "written": written,
            "kept": _values(rig, [o.signal for o in plan if o.signal not in values]),
        }

    # endregion

    # region Store

    def attach(self, store: Store) -> list[Latch]:
        """Keep latches in `store`; a latch an earlier run left re-applies its stop now.

        The runner's, once the store is open and the rig built, before serving. A rig stop's
        latch stops every device again; a fault's `stop` or `stop_device` stops what it
        held. Controllers start in manual regardless.
        """
        restored = self.latches.attach(store)
        if not restored:
            return restored
        rig = self.rig
        deadline = time.monotonic() + DEVICE_STOP_S
        for latch in restored:
            log.warning("latch kept from an earlier run: %s (%s)", latch.cause, latch.said())
            self.latch(latch)
        devices: dict[str, DeviceStop] = {}
        for latch in restored:
            if latch.cause == RIG_STOP:
                chosen = [d for d in list(rig.devices.values()) if stoppable(d)]
            elif latch.action in (FaultAction.STOP.value, FaultAction.STOP_DEVICE.value):
                chosen = self._held_devices(latch) + [s.device for s in self._held_signals(latch)]
            else:
                continue
            for device in chosen:
                if device.name in devices:
                    continue
                only = [s for s in self._held_signals(latch) if s.device is device] or None
                if only is not None:
                    with rig.lock:
                        devices[device.name] = self.stop_locked(device, only)
                else:
                    devices[device.name] = self.stop_device(device, deadline)
        if devices:
            _applied(rig, "a latch kept from an earlier run, re-applied at start", devices)
        return restored

    # endregion


class RigStopper:
    """The software stop: latch, cancel, interrupt, manual, then every device's resolved stop.

    What `POST /api/rig/stop`, MCP `stop_rig`, `flyball stop` and `SIGUSR1` run. Stops are
    serialised; a second one while the rig is latched latches nothing new and stops every
    device again. It never raises for one device: each is reported.
    """

    rig: Rig
    program: Program | None

    def __init__(self, rig: Rig, program: Program | None = None) -> None:
        self.rig = rig
        self.program = program
        self._lock = Lock()

    def stop(self, actor: Actor, reason: str) -> StopReport:
        at_ns = time.time_ns()
        rig = self.rig
        with self._lock:
            rig.stopping.latch(
                Latch(
                    cause=RIG_STOP,
                    subjects=subjects([("rig", rig.name or "rig")]),
                    by=actor.sub,
                    at_ns=at_ns,
                    reason=reason,
                )
            )
            report = self._run(actor, reason, at_ns, devices=None, why="stop")
        log.warning(
            "software stop by %s via %s (%s): program %s, %d controller(s) manual; %s",
            actor.sub,
            actor.via,
            reason or "no reason given",
            "interrupted" if report.program_interrupted else "not running",
            len(report.controllers_manual),
            ", ".join(f"{n} {d['state']}" for n, d in report.devices.items()) or "no devices",
        )
        return report

    def shutdown(self, actor: Actor, keep: bool = False) -> StopReport:
        """The runner's shutdown: each device's resolved stop unless it (or `keep`) says keep.

        Best-effort, within `DEVICE_STOP_S`; nothing is latched, so the next start is
        passive (each driver's build value). A device whose entry says `on_shutdown: keep`
        is left as it is, as is every device under `keep` (`--on-shutdown keep`).
        """
        rig = self.rig
        chosen: list[Device] = []
        if not keep:
            for device in list(rig.devices.values()):
                entry = rig.entries.get(device.name)
                if stoppable(device) and (entry is None or entry.on_shutdown != "keep"):
                    chosen.append(device)
        with self._lock:
            return self._run(actor, "shutdown", time.time_ns(), devices=chosen, why="shutdown")

    def _run(
        self, actor: Actor, reason: str, at_ns: int, *, devices: list[Device] | None, why: str
    ) -> StopReport:
        rig = self.rig
        for device in rig.running_commands():
            device.cancel()  # a dose or a move ends now; its own `finally` still runs
        interrupted = _interrupt(self.program, f"the rig was stopped ({why})")
        failed = _manual(rig, why)
        chosen = (
            [d for d in list(rig.devices.values()) if stoppable(d)] if devices is None else devices
        )
        stops = _stop_all(rig.stopping, chosen, time.monotonic() + DEVICE_STOP_S)
        for name, stop in stops.items():
            for address, error in failed.items():
                if address.partition(".")[0] == name:
                    stop["detail"] += f"; {error}"
        if stops:
            _applied(rig, f"{why} by {actor.sub}" + (f": {reason}" if reason else ""), stops)
        return StopReport(
            at_ns=at_ns,
            actor=actor,
            reason=reason,
            devices=stops,
            program_interrupted=interrupted,
            controllers_manual=_in_manual(rig),
            interim=False,
            latched=rig.stopping.latches.rig_stop is not None,
        )


def _stop_all(stopping: Stopping, devices: list[Device], deadline: float) -> dict[str, DeviceStop]:
    """Every device's stop at once, each on its own thread, all within `deadline`.

    Whatever is not done by then is `failed` and may still act (Python cannot cut a
    thread in a driver).
    """
    results: dict[str, DeviceStop] = {}

    def one(device: Device) -> None:
        try:
            results[device.name] = stopping.stop_device(device, deadline)
        except Exception as error:  # a stop never raises for one device
            log.exception("stop: %s", device.name)
            results[device.name] = {
                "state": "failed",
                "detail": f"{type(error).__name__}: {error}",
                "written": {},
                "kept": {},
            }

    threads = [
        threading.Thread(target=one, args=(d,), name=f"stop:{d.name}", daemon=True) for d in devices
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    out: dict[str, DeviceStop] = {}
    for device in devices:
        out[device.name] = results.get(device.name) or {
            "state": "failed",
            "detail": f"no answer within {DEVICE_STOP_S:g} s: may still act",
            "written": {},
            "kept": {},
        }
    return out


def _applied(rig: Rig, why: str, devices: Mapping[str, DeviceStop]) -> None:
    """The `stop_applied` event: what each device did, and every output left energised."""
    kept = {
        address: value
        for stop in devices.values()
        for address, value in stop.get("kept", {}).items()
    }
    counts = {
        state: sum(1 for d in devices.values() if d["state"] == state)
        for state in ("stopped", "unchanged", "failed")
    }
    rig.event(
        Severity.ERROR if counts["failed"] else Severity.WARNING,
        Scope.RIG,
        rig.name or "rig",
        Code.STOP_APPLIED,
        f"{why}: {counts['stopped']} stopped, {counts['unchanged']} unchanged,"
        f" {counts['failed']} failed; {len(kept)} output(s) left as they were",
        {"why": why, "devices": dict(devices), "kept": kept},
    )


def _interrupt(program: Program | None, reason: str) -> bool:
    if program is None:
        return False
    try:
        running = program.state.running
        program.interrupt(reason)
    except Exception:
        log.exception("stop: interrupting the program failed")
        return False
    return running


def _manual(rig: Rig, why: str | None) -> dict[str, str]:
    """Every controller to manual, with an `interrupted` event; by output address, what failed."""
    failed: dict[str, str] = {}
    for name, controller in list(rig.controllers.items()):
        was = controller.mode
        try:
            controller.manual()
        except Exception as error:
            log.exception("stop: controller %s would not go to manual", name)
            failed[controller.output_signal.address] = f"controller {name} not in manual: {error}"
            continue
        if why is not None and was is ControllerMode.REGULATING:
            rig.event(
                Severity.INFO,
                Scope.CONTROLLER,
                name,
                Code.INTERRUPTED,
                f"put in manual by the {why}",
                {"was": was.value, "by": why},
            )
    return failed


def _in_manual(rig: Rig) -> list[str]:
    return [
        name
        for name, controller in list(rig.controllers.items())
        if controller.mode is ControllerMode.MANUAL
    ]


def _values(rig: Rig, signals: Iterable[Signal]) -> dict[str, float | None]:
    """Each signal's last usable value, by address: what a kept output is left at."""
    out: dict[str, float | None] = {}
    for signal in signals:
        reading = rig.router.last_usable.get(signal)
        value = None if reading is None else reading.value
        out[signal.address] = value if isinstance(value, (int, float)) else None
    return out


def _kept_detail(plan: list[OutputStop]) -> str:
    said = [o.signal.name for o in plan if o.source == "you said"]
    nobody = [o.signal.name for o in plan if o.source == "nobody said"]
    parts = []
    if said:
        parts.append(f"kept as the rig file says ({', '.join(said)})")
    if nobody:
        parts.append(f"kept: no stop declared ({', '.join(nobody)})")
    return "; ".join(parts) or "nothing to write"


def _written_detail(values: Mapping[Signal, float], plan: list[OutputStop]) -> str:
    wrote = ", ".join(f"{s.name}={v:g}" for s, v in values.items())
    kept = [o.signal.name for o in plan if o.value is None]
    return f"wrote {wrote}" + (f"; kept {', '.join(kept)}" if kept else "")


def stop_plan(rig: Rig) -> list[dict[str, Any]]:
    """Every writable demand's resolved stop, sorted: `off (driver)`, `you said`, `keep`.

    With the controller that drives it, and why it is worth a look: a controller target
    no one gave a stop is left energised, open loop, after a stop; an unbounded `freeze`
    on an output whose stop is its driver's `off` holds it indefinitely.
    """
    order = {"off": 0, "you said": 1, "nobody said": 2}
    rows: list[dict[str, Any]] = []
    for device in list(rig.devices.values()):
        if not stoppable(device):
            continue
        command = type(device).stop_command
        for signal in outputs(device):
            resolved = resolve_output(signal)
            controller = rig.controllers.driving(signal)
            warnings: list[str] = []
            if command is None and controller is not None and resolved.source == "nobody said":
                warnings.append(
                    "a controller's output with no stop: after a stop its controller is in"
                    " manual and this output stays where it was (give `stop:` a value, or"
                    " `keep` to say so)"
                )
            if (
                controller is not None
                and controller.on_fault.action is FaultAction.FREEZE
                and resolved.source == "off"
            ):
                warnings.append(
                    "on_fault: freeze holds this output indefinitely while its source is"
                    " faulty; its stop is its driver's off"
                )
            rows.append({
                "address": signal.address,
                "device": device.name,
                "stop": None
                if command is not None
                else (resolved.value if resolved.value is not None else KEEP),
                "source": "command" if command is not None else resolved.source,
                "command": command,
                "controller": None if controller is None else controller.name,
                "covered_if_flyball_dies": False,
                "warnings": warnings,
            })
    rows.sort(key=lambda r: (r["source"] == "command", order.get(r["source"], 1), r["address"]))
    return rows
