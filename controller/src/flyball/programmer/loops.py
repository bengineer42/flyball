"""The controller commands every rig has: regulate, ramp, hold, arrive, manual.

Each names a controller by its target address -- or a list of them, or none
for the rig's default -- and does what the controller's own methods do, as a
program step and as `POST /api/programs/command`.
"""

from __future__ import annotations

from dataclasses import dataclass

from flyball.control import Controller
from flyball.control.setpoint import LinearRampSetpoint
from flyball.core import Operator
from flyball.core.clock import Duration, Speed
from flyball.runtime.rig import Rig

from .activities import Arrived, Timed
from .command import Activity, Command

ControllerNames = str | list[str] | None
"""One controller by target address, several, or None for the rig's default."""


def _controllers(rig: Rig, which: ControllerNames) -> list[Controller]:
    names = which if isinstance(which, list) else [which]
    return [rig.controllers.resolve(name) for name in names]


def _missing_controllers(rig: Rig, which: ControllerNames) -> list[str]:
    names = which if isinstance(which, list) else [which]
    out = []
    for name in names:
        if name is None:
            if rig.controllers.default is None:
                out.append("the rig has no default controller")
        elif name not in rig.controllers:
            out.append(f"controller {name!r} is not on the rig")
    return out


@dataclass(frozen=True)
class Regulate(Command, tag="regulate", primary="setpoint"):
    """Aim a controller at a setpoint and let its law drive; returns at once."""

    setpoint: float
    loop: ControllerNames = None
    tuning: str | None = None
    """A stored tuning to swap in first, bumplessly."""

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        tuning = None if self.tuning is None else rig.tunings.get(self.tuning)
        for controller in _controllers(rig, self.loop):
            controller.regulate(self.setpoint, tuning=tuning)
        return None

    def missing(self, rig: Rig) -> list[str]:
        out = _missing_controllers(rig, self.loop)
        if self.tuning is not None and rig.tunings.get(self.tuning) is None:
            out.append(f"tuning {self.tuning!r} is not stored")
        return out


@dataclass(frozen=True)
class Ramp(Command, tag="ramp", primary="to"):
    """Walk a controller's setpoint to `to` at `pace`, and wait until it arrives.

    `pace` is a rate (`per_minute: 5`) or how long the whole ramp should take
    (`minutes: 20`); a program file may write either flat beside `to`.
    """

    to: float
    pace: Speed | Duration
    loop: ControllerNames = None
    wait: bool = True
    """Wait for the ramp to arrive before the next step. False starts it and moves on;
    `arrive` can wait for it later."""

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        now_ns = rig.clock.now_ns()
        longest = 0.0
        controllers = _controllers(rig, self.loop)
        for controller in controllers:
            start = (
                controller.setpoint_at(now_ns)
                if controller.reference is not None
                else controller.last_value
            )
            if start is None:
                raise ValueError(f"controller {controller.name!r} has no reading yet to ramp from")
            if isinstance(self.pace, Speed):
                per_second = self.pace.per_second
                duration_s = abs(self.to - start) / per_second if per_second else 0.0
            else:
                duration_s = self.pace.seconds
            controller.regulate(
                start, generator=LinearRampSetpoint(self.pace, self.to), time_ns=now_ns
            )
            longest = max(longest, duration_s)
        names = ",".join(controller.name for controller in controllers)
        if not self.wait:
            return None
        return Timed(
            longest,
            name=f"ramp:{names}",
            message=f"{names} ramping to {self.to:g} over {longest:.0f} s",
        )

    def missing(self, rig: Rig) -> list[str]:
        return _missing_controllers(rig, self.loop)


@dataclass(frozen=True)
class Hold(Command, tag="hold", primary="duration"):
    """Keep everything as it is for `duration`; the controllers go on regulating.

    `timeout` (seconds; not a `Duration` -- `duration` is already the file's
    one flat-foldable field, and a second `Duration` field would make that
    ambiguous), like `wait`'s, ends the program instead if `duration` itself
    never elapses (a stalled clock, say).
    """

    duration: Duration
    message: str | None = None
    timeout: float | None = None

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        return Timed(
            self.duration.seconds,
            name="hold",
            message=self.message,
            timeout=self.timeout,
            clock=rig.clock,
        )


@dataclass(frozen=True)
class Arrive(Command, tag="arrive", primary="loop"):
    """Wait until the named controllers have settled within `within` of their setpoints.

    Judged on `readings` consecutive readings per controller; a ramp started
    with `wait: false` is waited out here, against where the ramp is at each
    reading. The other controllers carry on regulating meanwhile.
    """

    loop: ControllerNames = None
    within: float = 1.0
    readings: int = 3
    timeout: Duration | None = None
    message: str | None = None

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        controllers = _controllers(rig, self.loop)
        for controller in controllers:
            if controller.reference is None:
                raise ValueError(f"controller {controller.name!r} has no setpoint to arrive at")
        return Arrived(
            controllers,
            self.within,
            self.readings,
            timeout=self.timeout.seconds if self.timeout is not None else None,
            message=self.message,
            clock=rig.clock,
        )

    def missing(self, rig: Rig) -> list[str]:
        return _missing_controllers(rig, self.loop)


@dataclass(frozen=True)
class Manual(Command, tag="manual", primary="loop"):
    """Stop a controller regulating; its target keeps its last demand and takes demands directly."""

    loop: ControllerNames = None

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        for controller in _controllers(rig, self.loop):
            controller.manual()
        return None

    def missing(self, rig: Rig) -> list[str]:
        return _missing_controllers(rig, self.loop)


__all__ = ["Hold", "Manual", "Ramp", "Regulate"]
