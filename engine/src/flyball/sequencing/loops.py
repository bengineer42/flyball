"""The controller commands every rig has: regulate, ramp, wait, settle, manual.

Each names a controller by its output address -- or a list of them, or none
for the rig's default -- and does what the controller's own methods do, as a
program step and as `POST /api/programs/command`. A step that changes
controllers is one rig operation (`rig.regulate`, `rig.follow`, `rig.manual`):
the rig takes its own lock, so every controller named changes between the same
two deliveries, and a step never holds the lock itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from flyball.control.setpoint import LinearRampSetpoint
from flyball.foundation import Operator
from flyball.foundation.time import Duration, Speed
from flyball.model.controller import Controller
from flyball.model.errors import LastReadingNotAvailableError
from flyball.rig import Rig

from .activities import Settled, Timed
from .step import Activity, Step

ControllerNames = str | list[str] | None
"""One controller by output address, several, or None for the rig's default."""


def _controllers(rig: Rig, which: ControllerNames) -> list[Controller]:
    names = which if isinstance(which, list) else [which]
    return [rig.controllers.resolve(name) for name in names]


def _missing_controllers(rig: Rig, which: ControllerNames) -> list[str]:
    names = which if isinstance(which, list) else [which]
    out = []
    for name in names:
        if name is None:
            if rig.controllers.default_controller is None:
                out.append("the rig has no default controller")
        elif name not in rig.controllers:
            out.append(f"controller {name!r} is not on the rig")
    return out


@dataclass(frozen=True)
class Regulate(Step, type="regulate", primary="setpoint"):
    """Aim a controller at a setpoint and let its law drive; returns at once."""

    setpoint: float
    controllers: ControllerNames = None
    tuning: str | None = None
    """A stored tuning to swap in first, bumplessly."""

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        tuning = None if self.tuning is None else rig.tunings.get(self.tuning)
        rig.regulate(self.controllers, self.setpoint, law=tuning)
        return None

    def missing(self, rig: Rig) -> list[str]:
        out = _missing_controllers(rig, self.controllers)
        if self.tuning is not None and rig.tunings.get(self.tuning) is None:
            out.append(f"tuning {self.tuning!r} is not stored")
        return out


@dataclass(frozen=True)
class Ramp(Step, type="ramp", primary="to"):
    """Walk a controller's setpoint to `to` at `pace`, and wait until it arrives.

    `pace` is a rate (`per_minute: 5`) or how long the whole ramp should take
    (`minutes: 20`); a program file may write either flat beside `to`.
    """

    to: float
    pace: Speed | Duration
    controllers: ControllerNames = None
    wait: bool = True
    """Wait for the ramp to arrive before the next step. False starts it and moves on;
    `settle` can wait for it later."""

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        try:
            starts = rig.follow(self.controllers, lambda: LinearRampSetpoint(self.pace, self.to))
        except LastReadingNotAvailableError:
            # Which one: the first named with neither a setpoint nor a reading.
            for controller in _controllers(rig, self.controllers):
                if controller.reference is None and controller.last_value is None:
                    raise ValueError(
                        f"controller {controller.name!r} has no reading yet to ramp from"
                    ) from None
            raise
        longest = 0.0
        for start in starts.values():
            if isinstance(self.pace, Speed):
                per_second = self.pace.per_second
                duration_s = abs(self.to - start) / per_second if per_second else 0.0
            else:
                duration_s = self.pace.seconds
            longest = max(longest, duration_s)
        names = ",".join(controller.name for controller in starts)
        if not self.wait:
            return None
        return Timed(
            longest,
            name=f"ramp:{names}",
            message=f"{names} ramping to {self.to:g} over {longest:.0f} s",
        )

    def missing(self, rig: Rig) -> list[str]:
        return _missing_controllers(rig, self.controllers)


@dataclass(frozen=True)
class Wait(Step, type="wait", primary="duration"):
    """Keep everything as it is for `duration`; the controllers go on regulating.

    `timeout`, like `prompt`'s, ends the program instead if `duration` itself
    never elapses (a stalled clock, say).
    """

    duration: Duration
    message: str | None = None
    timeout: Duration | None = None

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        return Timed(
            self.duration.seconds,
            name="wait",
            message=self.message,
            timeout=self.timeout.seconds if self.timeout is not None else None,
            clock=rig.clock,
        )


@dataclass(frozen=True)
class Settle(Step, type="settle", primary="controllers"):
    """Wait until the named controllers have settled within `within` of their setpoints.

    Judged on `count` consecutive readings per controller; a ramp started
    with `wait: false` is waited out here, against where the ramp is at each
    reading. The other controllers carry on regulating meanwhile.
    """

    controllers: ControllerNames = None
    within: float = 1.0
    count: int = 3
    timeout: Duration | None = None
    message: str | None = None

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        controllers = _controllers(rig, self.controllers)
        for controller in controllers:
            if controller.reference is None:
                raise ValueError(f"controller {controller.name!r} has no setpoint to settle at")
        return Settled(
            controllers,
            self.within,
            self.count,
            timeout=self.timeout.seconds if self.timeout is not None else None,
            message=self.message,
            clock=rig.clock,
        )

    def missing(self, rig: Rig) -> list[str]:
        return _missing_controllers(rig, self.controllers)


@dataclass(frozen=True)
class Manual(Step, type="manual", primary="controllers"):
    """Stop a controller regulating; its output keeps its last value and takes demands directly."""

    controllers: ControllerNames = None

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        rig.manual(self.controllers)
        return None

    def missing(self, rig: Rig) -> list[str]:
        return _missing_controllers(rig, self.controllers)


__all__ = ["Manual", "Ramp", "Regulate", "Settle", "Wait"]
