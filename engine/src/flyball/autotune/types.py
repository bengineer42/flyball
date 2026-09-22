from __future__ import annotations

from dataclasses import dataclass
from math import exp, sqrt
from typing import NamedTuple, cast

from flyball.control import PI, PID
from flyball.foundation.typing import NonNegative, Positive
from flyball.library.tunings import Tuning
from flyball.model.law import ControlLawConfig


class Sample(NamedTuple):
    """One logged reading: what the process did, and when."""

    time: float
    value: float


@dataclass(frozen=True, slots=True)
class FOPDT:
    """First order plus dead time: `G(s) = gain·e^(-θs)/(τs + 1)`.

    How far the plant moves (`gain`), how fast (`tau`), and how long it waits
    first (`dead_time`). The model every rule in
    [flyball.autotune.rules][] takes. Run through the feedforward path, `gain`
    comes out near 1 because the actuator arithmetic has already scaled it.
    """

    gain: float
    tau: Positive
    dead_time: NonNegative
    error: float = 0.0
    """RMS residual of the fit, in reading units. Compare it against sensor noise."""

    @property
    def normalised_dead_time(self) -> float:
        """`θ/(θ+τ)`: 0 is pure lag, 1 is pure delay.

        How hard the plant is to control. Below 0.2 most tunings work; above
        0.6 no PID does well.
        """
        return self.dead_time / (self.dead_time + self.tau)

    def response(self, elapsed: float, size: float) -> float:
        """The change in reading `elapsed` after an input step of `size`, from rest."""
        after_delay = elapsed - self.dead_time
        if after_delay <= 0.0:
            return 0.0
        return self.gain * size * (1.0 - exp(-after_delay / self.tau))


@dataclass(frozen=True, slots=True)
class Ultimate:
    """The critical point: the gain and period at which the loop just oscillates."""

    gain: float
    """`Ku`: the proportional gain at which the loop is marginally stable."""

    period: float
    """`Tu`: the period of that oscillation."""


@dataclass(frozen=True, slots=True)
class Gains:
    """Controller gains in the parallel form the laws use.

    Rules are stated in ideal form (`Kp`, `Ti`, `Td`) and converted once by
    [of_ideal][flyball.autotune.types.Gains.of_ideal].
    """

    kp: float
    ki: float
    kd: float = 0.0
    tt: float = 0.0
    """Back-calculation tracking constant, for [IComponent][flyball.control.laws.IComponent]."""

    @classmethod
    def of_ideal(cls, kp: float, ti: float, td: float = 0.0) -> Gains:
        """Parallel-form gains from ideal-form `kp`, `ti`, `td` (zero for absent terms).

        `tt` is `√(Ti·Td)`, or `Ti` without a derivative term.
        """
        return cls(
            kp=kp,
            ki=kp / ti if ti else 0.0,
            kd=kp * td,
            tt=sqrt(ti * td) if td else ti,
        )

    @property
    def ti(self) -> float:
        """Integral time, back out of `ki`."""
        return self.kp / self.ki if self.ki else 0.0

    @property
    def td(self) -> float:
        """Derivative time, back out of `kd`."""
        return self.kd / self.kp if self.kp else 0.0

    @property
    def config(self) -> ControlLawConfig:
        """The law these gains are for.

        [PID][flyball.control.laws.PID] with derivative action, else
        [PI][flyball.control.laws.PI].
        """
        if self.kd:
            return cast(
                "ControlLawConfig", PID.config(kp=self.kp, ki=self.ki, kd=self.kd, tt=self.tt)
            )
        return cast("ControlLawConfig", PI.config(kp=self.kp, ki=self.ki, tt=self.tt))

    def to_tuning(self, tag: str) -> Tuning:
        """A tuning named `tag` wrapping [config][flyball.autotune.types.Gains.config]."""
        return Tuning(tag=tag, config=self.config)
