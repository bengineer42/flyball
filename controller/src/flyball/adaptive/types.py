from __future__ import annotations

from dataclasses import dataclass
from math import log

from flyball.core import Channel, Labelled, NonNegative, Positive

from .errors import ModelRejectedError


class Role(Labelled):
    """What a signal does in a loop's model."""

    CONTROLLED = "controlled", "The quantity being held"
    MANIPULATED = "manipulated", "The demand the loop commands"
    DISTURBANCE = "disturbance", "A measured input the loop cannot command"


@dataclass(frozen=True, slots=True)
class Term:
    """One input to a plant model, and what it is."""

    channel: Channel
    role: Role


@dataclass(frozen=True, slots=True)
class Schema:
    """Which signals a loop's model is built from.

    One controlled and one manipulated variable, plus any measured disturbances.
    Ordering is fixed here so a regressor and a parameter vector always line up;
    nothing downstream may reorder them.
    """

    controlled: Channel
    manipulated: Channel
    disturbances: tuple[Channel, ...] = ()

    @property
    def terms(self) -> tuple[Term, ...]:
        return (
            Term(self.controlled, Role.CONTROLLED),
            Term(self.manipulated, Role.MANIPULATED),
            *(Term(channel, Role.DISTURBANCE) for channel in self.disturbances),
        )

    @property
    def width(self) -> int:
        """How many parameters a model over this schema carries."""
        return 2 + len(self.disturbances)


@dataclass(frozen=True, slots=True)
class Arx:
    """A discrete first-order model with input delay.

    ``y[k] = a*y[k-1] + b*u[k-d] + sum(c[i]*w[i][k])``

    Linear in its parameters, which is the whole reason for this form: it is
    what lets the estimator recurse. The continuous parameters a tuning rule
    wants come from :meth:`plant`.
    """

    a: float
    b: float
    disturbance_gains: tuple[float, ...] = ()
    interval: Positive = 1.0
    delay_samples: int = 0

    def plant(self) -> Plant:
        """The continuous first-order plant this describes.

        Returns:
            Gain, time constant and dead time, in the units the tuning rules take.

        Raises:
            ModelRejectedError: ``a`` is outside ``(0, 1)``, so the discrete pole
                is not a stable first-order lag and the conversion is meaningless.
        """
        if not 0.0 < self.a < 1.0:
            raise ModelRejectedError(f"pole a={self.a:.4f} outside (0, 1)")
        return Plant(
            gain=self.b / (1.0 - self.a),
            tau=-self.interval / log(self.a),
            dead_time=self.delay_samples * self.interval,
        )


@dataclass(frozen=True, slots=True)
class Plant:
    """A first-order-plus-dead-time plant, as the tuning rules take it."""

    gain: float
    tau: Positive
    dead_time: NonNegative = 0.0

    @property
    def controllability(self) -> float:
        """Dead time over time constant. Above ~1 the plant is hard to control."""
        return self.dead_time / self.tau if self.tau else float("inf")

    def within(self, other: Plant, tolerance: float) -> bool:
        """Whether ``other`` is within ``tolerance`` (fractional) of this one.

        Used to decide whether a fresh fit is a drift worth retuning for or
        noise worth ignoring.
        """
        return abs(other.gain - self.gain) <= tolerance * abs(self.gain) and (
            abs(other.tau - self.tau) <= tolerance * self.tau
        )
