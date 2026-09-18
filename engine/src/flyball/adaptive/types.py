from __future__ import annotations

from dataclasses import dataclass
from math import log

from flyball.core import Labelled, NonNegative, Positive, Signal

from .errors import ModelRejectedError


class Role(Labelled):
    """What a signal does in a loop's model."""

    CONTROLLED = "controlled", "The quantity being held"
    MANIPULATED = "manipulated", "The demand the loop commands"
    DISTURBANCE = "disturbance", "A measured input the loop cannot command"


@dataclass(frozen=True, slots=True)
class Term:
    """One input to a plant model, and what it is."""

    signal: Signal
    role: Role


@dataclass(frozen=True, slots=True)
class Schema:
    """Which signals a loop's model is built from: controlled, manipulated, disturbances.

    The order is fixed here so regressors and parameter vectors line up.
    """

    controlled: Signal
    manipulated: Signal
    disturbances: tuple[Signal, ...] = ()

    @property
    def terms(self) -> tuple[Term, ...]:
        return (
            Term(self.controlled, Role.CONTROLLED),
            Term(self.manipulated, Role.MANIPULATED),
            *(Term(signal, Role.DISTURBANCE) for signal in self.disturbances),
        )

    @property
    def width(self) -> int:
        """How many parameters a model over this schema carries: `a`, `b`, the offset, the gains."""
        return 3 + len(self.disturbances)


@dataclass(frozen=True, slots=True)
class Arx:
    """A discrete first-order model with input delay and an operating point.

    `y[k] = a*y[k-1] + b*u[k-d] + offset + sum(c[i]*w[i][k])`. Linear in its
    parameters, which lets the estimator recurse. The offset is what a plant
    rests at with no input -- a room's temperature under a heater, a chiller's
    ambient -- folded into one term: `(1 - a) * ambient`. Without it a plant
    that does not rest at zero cannot be fitted at all, and a loop whose
    demand is already in the controlled quantity's units (a "smart" drive) has
    an offset near zero and loses nothing.
    [plant][flyball.adaptive.types.Arx.plant] gives the continuous form.
    """

    a: float
    b: float
    offset: float = 0.0
    disturbance_gains: tuple[float, ...] = ()
    interval: Positive = 1.0
    delay_samples: int = 0

    def plant(self) -> Plant:
        """The continuous first-order plant this describes.

        Raises:
            ModelRejectedError: `a` is outside `(0, 1)`: not a stable
                first-order lag.
        """
        if not 0.0 < self.a < 1.0:
            raise ModelRejectedError(f"pole a={self.a:.4f} outside (0, 1)")
        return Plant(
            gain=self.b / (1.0 - self.a),
            tau=-self.interval / log(self.a),
            dead_time=self.delay_samples * self.interval,
            ambient=self.offset / (1.0 - self.a),
        )


@dataclass(frozen=True, slots=True)
class Plant:
    """A first-order-plus-dead-time plant, as the tuning rules take it.

    `ambient` is where the output rests with no input; steady state is
    `ambient + gain * input`.
    """

    gain: float
    tau: Positive
    dead_time: NonNegative = 0.0
    ambient: float = 0.0

    @property
    def controllability(self) -> float:
        """Dead time over time constant. Above ~1 the plant is hard to control."""
        return self.dead_time / self.tau if self.tau else float("inf")

    def within(self, other: Plant, tolerance: float) -> bool:
        """Whether `other` is within fractional `tolerance` of this one."""
        return abs(other.gain - self.gain) <= tolerance * abs(self.gain) and (
            abs(other.tau - self.tau) <= tolerance * self.tau
        )
