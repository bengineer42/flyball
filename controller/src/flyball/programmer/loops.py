"""The loop commands every rig has: regulate, ramp, hold, arrive, manual.

Each names a loop -- or a list of them, or none for the rig's default -- and
does what the loop's own methods do, as a program step and as
`POST /api/programs/command`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flyball.control.setpoint import LinearRampSetpoint
from flyball.core import Operator
from flyball.core.clock import Duration, Speed
from flyball.runtime.rig import Rig

from .activities import Arrived, Timed
from .command import Activity, Command

Loops = str | list[str] | None
"""One loop by name, several, or None for the rig's default."""


def _loops(rig: Rig, which: Loops) -> list[Any]:
    names = which if isinstance(which, list) else [which]
    return [rig.loops.resolve(name) for name in names]


@dataclass(frozen=True)
class Regulate(Command, tag="regulate", primary="setpoint"):
    """Aim a loop at a setpoint and let its law drive; returns at once."""

    setpoint: float
    loop: Loops = None
    tuning: str | None = None
    """A stored tuning to swap in first, bumplessly."""

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        for loop in _loops(rig, self.loop):
            loop.regulate(self.setpoint, tuning=self.tuning)
        return None


@dataclass(frozen=True)
class Ramp(Command, tag="ramp", primary="to"):
    """Walk a loop's setpoint to `to` at `pace`, and wait until it arrives.

    `pace` is a rate (`per_minute: 5`) or how long the whole ramp should take
    (`minutes: 20`); a program file may write either flat beside `to`.
    """

    to: float
    pace: Speed | Duration
    loop: Loops = None
    wait: bool = True
    """Wait for the ramp to arrive before the next step. False starts it and moves on;
    `arrive` can wait for it later."""

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        now_ns = rig.clock.now_ns()
        longest = 0.0
        loops = _loops(rig, self.loop)
        for loop in loops:
            start = loop.setpoint_at(now_ns) if loop.reference is not None else loop.last_value
            if start is None:
                raise ValueError(f"loop {loop.name!r} has no reading yet to ramp from")
            if isinstance(self.pace, Speed):
                per_second = self.pace.per_second
                duration_s = abs(self.to - start) / per_second if per_second else 0.0
            else:
                duration_s = self.pace.seconds
            loop.regulate(start, generator=LinearRampSetpoint(self.pace, self.to), time_ns=now_ns)
            longest = max(longest, duration_s)
        names = ",".join(loop.name for loop in loops)
        if not self.wait:
            return None
        return Timed(
            longest,
            name=f"ramp:{names}",
            message=f"{names} ramping to {self.to:g} over {longest:.0f} s",
        )


@dataclass(frozen=True)
class Hold(Command, tag="hold", primary="duration"):
    """Keep everything as it is for `duration`; the loops go on regulating."""

    duration: Duration
    message: str | None = None

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        return Timed(self.duration.seconds, name="hold", message=self.message)


@dataclass(frozen=True)
class Arrive(Command, tag="arrive", primary="loop"):
    """Wait until the named loops have settled within `within` of their setpoints.

    Judged on `readings` consecutive readings per loop; a ramp started with
    `wait: false` is waited out here, against where the ramp is at each
    reading. The other loops carry on regulating meanwhile.
    """

    loop: Loops = None
    within: float = 1.0
    readings: int = 3
    timeout: Duration | None = None
    message: str | None = None

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        loops = _loops(rig, self.loop)
        for loop in loops:
            if loop.reference is None:
                raise ValueError(f"loop {loop.name!r} has no setpoint to arrive at")
        return Arrived(
            [(rig.loops.channel(loop.name), loop) for loop in loops],
            self.within,
            self.readings,
            timeout=self.timeout.seconds if self.timeout is not None else None,
            message=self.message,
            clock=rig.clock,
        )


@dataclass(frozen=True)
class Manual(Command, tag="manual", primary="loop"):
    """Stop a loop regulating; its actuator keeps its last demand and takes commands directly."""

    loop: Loops = None

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        for loop in _loops(rig, self.loop):
            loop.manual()
        return None


__all__ = ["Hold", "Manual", "Ramp", "Regulate"]
