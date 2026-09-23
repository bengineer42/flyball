"""The stop boundary: who asked for a stop, what it did, and the interim stop.

Here rather than in `flyball.runner.stopping` so the server's stop route can name
them: the server sits below the runner and may not import it. `flyball.runner.stopping`
re-exports them beside the break-glass. What a stop does to outputs is not decided
here; whatever implements [Stopper][flyball.rig.stopping.Stopper] decides it.
[InterimStopper][flyball.rig.stopping.InterimStopper] is what runs until the signals
work supplies the real one: it writes nothing.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from threading import Lock
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict

from flyball.model.controller import ControllerMode

if TYPE_CHECKING:
    from .rig import Rig

__all__ = ["Actor", "DeviceStop", "InterimStopper", "Program", "StopReport", "Stopper"]

log = logging.getLogger("flyball.stop")


@dataclasses.dataclass(frozen=True)
class Actor:
    """Who asked for a stop: the principal's `sub`/`sid`/`kind`, and the way it came in."""

    sub: str
    sid: str
    kind: str
    via: Literal["http", "mcp", "signal"]
    detail: str = ""
    """Anything more about the caller, e.g. `signal from pid 4121 uid 1000`."""


class DeviceStop(TypedDict):
    """What a stop did to one device."""

    state: Literal["stopped", "unchanged", "failed"]
    detail: str


@dataclasses.dataclass(frozen=True)
class StopReport:
    """What one stop did, device by device."""

    at_ns: int
    """Wall-clock time the stop began, ns since the epoch (not the rig's clock)."""
    actor: Actor
    reason: str
    devices: dict[str, DeviceStop]
    """Every device with a writable signal, by name; one with none has nothing to stop."""
    program_interrupted: bool
    """Whether this stop found a program running and interrupted it."""
    controllers_manual: list[str]
    """Every controller in manual once the stop was done, by name."""
    interim: bool
    """True until the real stop (the signals work's) replaces the interim one."""

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
    def interrupt(self) -> None: ...


UNCHANGED = "interim stop: controllers to manual; nothing written — outputs left as they were"


class InterimStopper:
    """The stop until the signals work's lands: interrupt, every controller to manual, hold.

    Writes nothing to any device, so every writable device is reported `unchanged`, never
    `stopped` -- and never "safe": what the outputs are left doing is whatever they
    were last told. Not [Rig.stop][flyball.rig.rig.Rig.stop], which is a teardown.

    The program is interrupted first, so no step runs after the controllers go to
    manual; a step that blocks without answering the interrupt holds the stop up with it
    (signal-faults S6 makes blocking commands pre-emptable). Stops are serialised; a
    second one finds nothing running and changes nothing.
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
            interrupted = self._interrupt()
            failed = self._manual()
        manual = [
            name
            for name, controller in list(self.rig.controllers.items())
            if controller.mode is ControllerMode.MANUAL
        ]
        devices: dict[str, DeviceStop] = {}
        for name, device in list(self.rig.devices.items()):
            writables = sorted(device.writables)
            if not writables:
                continue
            errors = [failed[s.address] for s in device.writables.values() if s.address in failed]
            if errors:
                devices[name] = {"state": "failed", "detail": "; ".join(errors)}
            else:
                devices[name] = {
                    "state": "unchanged",
                    "detail": f"{UNCHANGED} ({', '.join(writables)})",
                }
        report = StopReport(
            at_ns=at_ns,
            actor=actor,
            reason=reason,
            devices=devices,
            program_interrupted=interrupted,
            controllers_manual=manual,
            interim=True,
        )
        log.warning(
            "software stop by %s via %s (%s): program %s, %d controller(s) manual, "
            "%d device(s) unchanged",
            actor.sub,
            actor.via,
            reason or "no reason given",
            "interrupted" if interrupted else "not running",
            len(manual),
            sum(d["state"] == "unchanged" for d in devices.values()),
        )
        return report

    def _interrupt(self) -> bool:
        if self.program is None:
            return False
        try:
            running = self.program.state.running
            self.program.interrupt()
        except Exception:
            log.exception("stop: interrupting the program failed")
            return False
        return running

    def _manual(self) -> dict[str, str]:
        """Every controller to manual; by output address, why one would not go."""
        failed: dict[str, str] = {}
        for name, controller in list(self.rig.controllers.items()):
            try:
                controller.manual()
            except Exception as error:
                log.exception("stop: controller %s would not go to manual", name)
                failed[controller.output_signal.address] = (
                    f"controller {name} not in manual: {error}"
                )
        return failed
