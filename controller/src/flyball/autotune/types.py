from __future__ import annotations

from dataclasses import dataclass
from math import exp, sqrt
from typing import NamedTuple, cast

from flyball.control import PI, PID, ControlLawConfig, Tuning
from flyball.core.typing import NonNegative, Positive


class Sample(NamedTuple):
    """One logged reading: what the process did, and when."""

    time: float
    value: float


@dataclass(frozen=True, slots=True)
class FOPDT:
    """First order plus dead time: ``G(s) = gain·e^(-θs)/(τs + 1)``.

    Three numbers are enough to describe the rig for tuning purposes: how far it
    moves (``gain``), how fast (``tau``), and how long it waits first
    (``dead_time``). Every rule in :mod:`flyball.autotune.rules` that takes a
    plant model takes this one.

    Physically, for a well-mixed chamber ``tau`` is the residence time V/Q and
    ``dead_time`` is tube transport plus sensor response. Run against the
    feedforward path the ``gain`` comes out near 1, because
    :func:`~flyball.controller.calculate_wet_fraction` has already divided out
    the ``wet - dry`` span.
    """

    gain: float
    tau: Positive
    dead_time: NonNegative
    error: float = 0.0
    """RMS residual of the fit, in reading units. Compare it against sensor noise."""

    @property
    def normalised_dead_time(self) -> float:
        """``θ/(θ+τ)``: 0 is pure lag, 1 is pure delay.

        The single number that says how hard the plant is to control. Below 0.2
        almost any tuning works; above 0.6 no PID does well and the answer is
        shorter tubing, not better gains.
        """
        return self.dead_time / (self.dead_time + self.tau)

    def response(self, elapsed: float, size: float) -> float:
        """The change in reading ``elapsed`` after a step of ``size``, from rest.

        Args:
            elapsed: Time since the step.
            size: How far the input moved.

        Returns:
            How far the reading has moved by then.
        """
        after_delay = elapsed - self.dead_time
        if after_delay <= 0.0:
            return 0.0
        return self.gain * size * (1.0 - exp(-after_delay / self.tau))


@dataclass(frozen=True, slots=True)
class Ultimate:
    """The critical point: the gain and period at which the loop just oscillates.

    What a relay test measures directly, without ever fitting a model.
    """

    gain: float
    """``Ku``: the proportional gain at which the loop is marginally stable."""

    period: float
    """``Tu``: the period of that oscillation."""


@dataclass(frozen=True, slots=True)
class Gains:
    """Controller gains in the parallel form the laws are written in.

    Tuning rules are stated in the ideal form (``Kp``, ``Ti``, ``Td``) in every
    source worth quoting, so they are written that way here and converted once,
    by :meth:`of_ideal`, rather than transcribed pre-multiplied.
    """

    kp: float
    ki: float
    kd: float = 0.0
    tt: float = 0.0
    """Back-calculation tracking constant, for :class:`~flyball.controller.laws.IComponent`."""

    @classmethod
    def of_ideal(cls, kp: float, ti: float, td: float = 0.0) -> Gains:
        """Gains for a rule stated as proportional gain and integral/derivative times.

        Args:
            kp: Proportional gain.
            ti: Integral time. Zero for a law with no integral action.
            td: Derivative time. Zero for PI.

        Returns:
            The same controller in parallel form, with ``tt`` set to the usual
            ``√(Ti·Td)``, or to ``Ti`` when there is no derivative term.
        """
        return cls(
            kp=kp,
            ki=kp / ti if ti else 0.0,
            kd=kp * td,
            tt=sqrt(ti * td) if td else ti,
        )

    @property
    def ti(self) -> float:
        """Integral time, back out of ``ki``."""
        return self.kp / self.ki if self.ki else 0.0

    @property
    def td(self) -> float:
        """Derivative time, back out of ``kd``."""
        return self.kd / self.kp if self.kp else 0.0

    @property
    def config(self) -> ControlLawConfig:
        """The law these gains describe: :class:`PID` with derivative action, else :class:`PI`."""
        if self.kd:
            return cast(
                "ControlLawConfig", PID.config(kp=self.kp, ki=self.ki, kd=self.kd, tt=self.tt)
            )
        return cast("ControlLawConfig", PI.config(kp=self.kp, ki=self.ki, tt=self.tt))

    def to_tuning(self, tag: str) -> Tuning:
        """The gains as a named tuning, ready to register or hand to a controller.

        Args:
            tag: The name to file it under.

        Returns:
            A tuning wrapping :attr:`config`.
        """
        return self.config.to_tuning(tag)
