"""The controller commands every rig has: regulate, ramp, wait, settle, manual.

Each names a controller by its output address -- or a list of them, or none
for the rig's default -- and does what the controller's own methods do, as a
program step and as `POST /api/programs/command`. `settle` may name a signal
instead, and wait for its readings to meet a
[Criterion][flyball.sequencing.criterion.Criterion].
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from flyball.control.setpoint import LinearRampSetpoint
from flyball.foundation import Operator
from flyball.foundation.device import Access, AddressNotFoundError, OnNoValue, Signal
from flyball.foundation.time import Duration, Speed
from flyball.model.controller import Controller
from flyball.rig import Rig

from .activities import CriterionMet, Settled, Timed
from .criterion import Criterion
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
            if rig.controllers.default is None:
                out.append("the rig has no default controller")
        elif name not in rig.controllers:
            out.append(f"controller {name!r} is not on the rig")
    return out


@dataclass(frozen=True)
class Regulate(Step, tag="regulate", primary="setpoint"):
    """Aim a controller at a setpoint and let its law drive; returns at once."""

    setpoint: float
    controllers: ControllerNames = None
    tuning: str | None = None
    """A stored tuning to swap in first, bumplessly."""

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        tuning = None if self.tuning is None else rig.tunings.get(self.tuning)
        for controller in _controllers(rig, self.controllers):
            controller.regulate(self.setpoint, tuning=tuning)
        return None

    def missing(self, rig: Rig) -> list[str]:
        out = _missing_controllers(rig, self.controllers)
        if self.tuning is not None and rig.tunings.get(self.tuning) is None:
            out.append(f"tuning {self.tuning!r} is not stored")
        return out


@dataclass(frozen=True)
class Ramp(Step, tag="ramp", primary="to"):
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
        now_ns = rig.clock.now_ns()
        longest = 0.0
        controllers = _controllers(rig, self.controllers)
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
        return _missing_controllers(rig, self.controllers)


@dataclass(frozen=True)
class Wait(Step, tag="wait", primary="duration"):
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


_CRITERION_FIELDS = ("above", "below", "near", "from_start", "on_no_value")
"""`settle`'s fields that make sense only with `signal:`."""


@dataclass(frozen=True)
class Settle(Step, tag="settle", primary="controllers"):
    """Wait until controllers have settled at their setpoints, or until a signal meets a test.

    Either form, never both. Named `controllers` (or none, for the rig's
    default): each must read within `within` of its own setpoint (default 1)
    for `count` consecutive readings; a ramp started with `wait: false` is
    waited out here, against where the ramp is at each reading. Named a
    `signal`: its readings must meet one of `above`, `below`, or `near` with
    `within` for `count` readings running -- a signal with no controller
    (a sample, a weight, a count). With `from_start`, the number is relative
    to the signal's value when the step starts. A reading at a limit resets
    the count; one with no value is not met unless `on_no_value` is `fire`.
    The controllers carry on regulating meanwhile. `timeout` gives up and
    fails the program.
    """

    controllers: ControllerNames = None
    within: float | None = None
    count: int = 3
    timeout: Duration | None = None
    message: str | None = None
    signal: str | None = None
    above: float | None = None
    below: float | None = None
    near: float | None = None
    from_start: bool = False
    on_no_value: OnNoValue | None = None

    def __post_init__(self) -> None:
        if self.signal is None:
            given = [name for name in _CRITERION_FIELDS if getattr(self, name) not in (None, False)]
            if given:
                raise ValueError(f"settle: {given} need a `signal:` to test")
            return
        if self.controllers is not None:
            raise ValueError(
                "settle waits on `controllers` or on a `signal`, not both: write two steps"
            )
        try:
            self.criterion()
        except ValidationError as e:  # said as a step's refusal, not as a model's
            why = "; ".join(str(error["msg"]).removeprefix("Value error, ") for error in e.errors())
            raise ValueError(f"settle: {why}") from None

    def criterion(self) -> Criterion | None:
        """The test on `signal`; None for the controller form.

        Raises:
            ValidationError: Not exactly one of `above`, `below` and `near`, or
                `near` without `within`.
        """
        if self.signal is None:
            return None
        return Criterion(
            signal=self.signal,
            above=self.above,
            below=self.below,
            near=self.near,
            within=self.within,
            from_start=self.from_start,
            on_no_value=self.on_no_value,
        )

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        timeout = self.timeout.seconds if self.timeout is not None else None
        if (criterion := self.criterion()) is not None:
            # Resolved once, here, under the rig's lock: the activity reads its start value
            # from the binding before any later delivery can reach it.
            binding = rig.follow(
                criterion.signal,
                owner=operator if operator is not None else "program",
                name="settle",
            )
            if binding.signal is None:
                rig.unbind(binding)
                raise ValueError(f"settle: '{criterion.signal}' is a namespace, not a signal")
            return CriterionMet(
                binding,
                criterion,
                self.count,
                timeout=timeout,
                message=self.message,
                clock=rig.clock,
            )
        controllers = _controllers(rig, self.controllers)
        for controller in controllers:
            if controller.reference is None:
                raise ValueError(f"controller {controller.name!r} has no setpoint to settle at")
        return Settled(
            controllers,
            1.0 if self.within is None else self.within,
            self.count,
            timeout=timeout,
            message=self.message,
            clock=rig.clock,
        )

    def missing(self, rig: Rig) -> list[str]:
        if self.signal is not None:
            try:
                found = rig.resolve(self.signal)
            except AddressNotFoundError as e:
                return [str(e)]
            if not isinstance(found, Signal):
                return [f"'{found.address}' is a namespace, not a signal"]
            if Access.P not in found.access:
                return [f"'{found.address}' [{found.access}] is not published"]
            return []
        return _missing_controllers(rig, self.controllers)


@dataclass(frozen=True)
class Manual(Step, tag="manual", primary="controllers"):
    """Stop a controller regulating; its output keeps its last value and takes demands directly."""

    controllers: ControllerNames = None

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        for controller in _controllers(rig, self.controllers):
            controller.manual()
        return None

    def missing(self, rig: Rig) -> list[str]:
        return _missing_controllers(rig, self.controllers)


__all__ = ["Manual", "Ramp", "Regulate", "Settle", "Wait"]
