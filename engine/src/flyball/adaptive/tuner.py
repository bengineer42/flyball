from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from flyball.foundation import Labelled, NonNegative, NormalisedPositive, Positive, PositiveInt

from .errors import AdaptiveError
from .identifier import Identifier
from .types import Plant


class Verdict(Labelled):
    """Why a retune was or was not offered."""

    OFFERED = "offered", "A new tuning is ready"
    UNIDENTIFIED = "unidentified", "Too few excited samples yet"
    IMPLAUSIBLE = "implausible", "The fit is outside the bounds a plant may take"
    UNCHANGED = "unchanged", "The model has not drifted enough to be worth it"
    TOO_SOON = "too_soon", "Retuned recently; the loop has not settled"
    DIVERGING = "diverging", "The residual is growing, so the model is not trusted"


@dataclass(frozen=True, slots=True)
class Retune:
    """The outcome of one retune attempt."""

    verdict: Verdict
    plant: Plant | None = None
    residual: float = 0.0

    @property
    def offered(self) -> bool:
        return self.verdict is Verdict.OFFERED


@dataclass(frozen=True, slots=True)
class Bounds:
    """What a plausible plant looks like; a fit outside is refused, not applied.

    Set generously from the commissioning step test.
    """

    gain: tuple[Positive, Positive] = (1e-3, 1e3)
    tau: tuple[Positive, Positive] = (0.1, 3_600.0)

    def holds(self, plant: Plant) -> bool:
        low, high = self.gain
        if not low <= abs(plant.gain) <= high:
            return False
        low, high = self.tau
        return low <= plant.tau <= high


class SelfTuner[T]:
    """Watches a model drift and offers a new tuning when warranted.

    Runs on a much slower clock than the identifier: retuning faster than the
    plant settles makes the two loops interact, so `settling_periods` is a
    floor. Applies nothing; the caller derives gains and hands them over, so a
    retune takes the same bumpless path as any tuning change.

    Args:
        identifier: The estimator to read.
        rule: Turns a plant into the caller's tuning type.
        bounds: What counts as a plausible plant.
        drift: Fractional change in gain or time constant worth retuning for.
        settling_periods: Minimum retune spacing, in time constants of the
            model in force.
        residual_growth: Ratio of the residual's running level to the best
            level seen since the last retune, above which the model no longer
            describes the plant.
        smoothing: Weight of each new residual in the running level.
    """

    __slots__ = (
        "_applied",
        "_best_level",
        "_fitted",
        "_level",
        "_observed",
        "_since",
        "bounds",
        "drift",
        "identifier",
        "residual_growth",
        "rule",
        "settling_periods",
        "smoothing",
    )

    WARM_UP = 20
    """Observations before the running level counts as a best: the fit finding its feet."""

    def __init__(
        self,
        identifier: Identifier,
        rule: Callable[[Plant], T],
        bounds: Bounds | None = None,
        drift: Positive = 0.15,
        settling_periods: PositiveInt = 5,
        residual_growth: Positive = 3.0,
        smoothing: NormalisedPositive = 0.05,
    ) -> None:
        self.identifier = identifier
        self.rule = rule
        self.bounds = bounds or Bounds()
        self.drift = drift
        self.settling_periods = settling_periods
        self.residual_growth = residual_growth
        self.smoothing = smoothing
        self._applied: Plant | None = None
        self._level: float = 0.0
        self._fitted = 0
        self._best_level: float | None = None
        self._observed = 0
        self._since: float = 0.0

    @property
    def applied(self) -> Plant | None:
        """The model the tuning in force was derived from."""
        return self._applied

    @property
    def residual_level(self) -> float:
        """The running magnitude of the prediction error: noise when the model fits."""
        return self._level

    def observe(self) -> None:
        """Take the identifier's latest prediction error for the divergence check.

        Call it every tick: a tick the identifier did not fit (the loop was
        not excited) is skipped here, or a stale error would pull the level
        down to nothing between transients. A single error says little -- it
        crosses zero on every fit -- so the check compares a running level
        against the best level seen since the model in force was accepted.
        """
        fitted = self.identifier.excited_samples
        if fitted == self._fitted:
            return
        self._fitted = fitted
        self._level += self.smoothing * (abs(self.identifier.residual) - self._level)
        self._observed += 1
        if self._observed < self.WARM_UP:
            return
        if self._best_level is None or self._level < self._best_level:
            self._best_level = self._level

    def elapsed(self, seconds: NonNegative) -> None:
        """Advance the retune spacing clock."""
        self._since += seconds

    def consider(self) -> Retune:
        """The verdict, and the plant it was based on when a retune is offered. Applies nothing."""
        residual = self.identifier.residual
        if not self.identifier.identified:
            return Retune(Verdict.UNIDENTIFIED, residual=residual)
        if self._diverging():
            return Retune(Verdict.DIVERGING, residual=residual)

        try:
            plant = self.identifier.plant()
        except AdaptiveError:
            return Retune(Verdict.IMPLAUSIBLE, residual=residual)

        if not self.bounds.holds(plant):
            return Retune(Verdict.IMPLAUSIBLE, plant, residual)
        if self._applied is not None:
            if (plant.gain > 0) != (self._applied.gain > 0):
                # A plant does not change the direction it responds in; a fit
                # that says so is fitting noise, or a loop that has not moved.
                return Retune(Verdict.IMPLAUSIBLE, plant, residual)
            if self._since < self.settling_periods * self._applied.tau:
                return Retune(Verdict.TOO_SOON, plant, residual)
            if self._applied.within(plant, self.drift):
                return Retune(Verdict.UNCHANGED, plant, residual)
        return Retune(Verdict.OFFERED, plant, residual)

    def accept(self, plant: Plant) -> T:
        """Record `plant` as the model in force and return its tuning. Call once committed.

        The divergence check starts over: the best level is the new model's to set.
        """
        self._applied = plant
        self._since = 0.0
        self._best_level = None
        self._observed = 0
        return self.rule(plant)

    def _diverging(self) -> bool:
        best = self._best_level
        if best is None or not best:
            return False
        return self._level > self.residual_growth * best
