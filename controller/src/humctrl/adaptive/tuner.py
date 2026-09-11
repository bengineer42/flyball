from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from humctrl.core import Labelled, NonNegative, Positive, PositiveInt

from .errors import AdaptiveError
from .identifier import Identifier
from .types import Plant


class Verdict(Labelled):
    """Why a retune was or was not offered."""

    OFFERED = "offered", "A new tuning is ready"
    UNIDENTIFIED = "unidentified", "Too few excited samples yet"
    IMPLAUSIBLE = "implausible", "The fit is outside the bounds a plant may take"
    UNCHANGED = "unchanged", "The model has not drifted enough to be worth it"
    TOO_SOON = "too soon", "Retuned recently; the loop has not settled"
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
    """What a plausible plant looks like, so a bad fit is refused not applied.

    A fit outside these is a broken estimate, not a changed plant. Set them from
    the commissioning step test, generously -- they are a sanity check, not a
    specification.
    """

    gain: tuple[Positive, Positive] = (1e-3, 1e3)
    tau: tuple[Positive, Positive] = (0.1, 3_600.0)

    def holds(self, plant: Plant) -> bool:
        low, high = self.gain
        if not low <= abs(plant.gain) <= high:
            return False
        low, high = self.tau
        return low <= plant.tau <= high


class SelfTuner:
    """Watches a model drift and offers a new tuning when it is worth it.

    The slow half of a self-tuning regulator: the identifier runs every sample,
    this runs on a much longer clock. Retuning faster than the plant settles
    makes the two loops interact, which is the usual way adaptive control goes
    unstable -- so ``settling_periods`` is a floor, not a preference.

    Nothing here applies anything. It returns a plant model and leaves the
    caller to derive gains and hand them over, so a retune goes through the same
    bumpless path as any other tuning change and reports its own bump.

    Args:
        identifier: The estimator to read.
        rule: Turns a plant into whatever the caller's tuning type is.
        bounds: What counts as a plausible plant.
        drift: Fractional change in gain or time constant worth retuning for.
        settling_periods: Minimum retune spacing, in time constants of the
            model in force.
        residual_growth: Ratio of current to best residual above which the model
            is treated as no longer describing the plant.
    """

    __slots__ = (
        "_applied",
        "_best_residual",
        "_since",
        "bounds",
        "drift",
        "identifier",
        "residual_growth",
        "rule",
        "settling_periods",
    )

    def __init__[T](
        self,
        identifier: Identifier,
        rule: Callable[[Plant], T],
        bounds: Bounds | None = None,
        drift: Positive = 0.15,
        settling_periods: PositiveInt = 5,
        residual_growth: Positive = 3.0,
    ) -> None:
        self.identifier = identifier
        self.rule = rule
        self.bounds = bounds or Bounds()
        self.drift = drift
        self.settling_periods = settling_periods
        self.residual_growth = residual_growth
        self._applied: Plant | None = None
        self._best_residual: float | None = None
        self._since: float = 0.0

    @property
    def applied(self) -> Plant | None:
        """The model the tuning in force was derived from."""
        return self._applied

    def observe(self, residual: float) -> None:
        """Record a prediction error, for the divergence check.

        Tracked as a floor rather than an average: what matters is whether the
        model has stopped fitting as well as it once did, not its average error.
        """
        magnitude = abs(residual)
        if self._best_residual is None or magnitude < self._best_residual:
            self._best_residual = magnitude

    def elapsed(self, seconds: NonNegative) -> None:
        """Advance the retune spacing clock."""
        self._since += seconds

    def consider(self) -> Retune:
        """Decide whether a retune is warranted, without applying one.

        Returns:
            The verdict, and the plant it was based on when one was offered.
        """
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
            if self._since < self.settling_periods * self._applied.tau:
                return Retune(Verdict.TOO_SOON, plant, residual)
            if self._applied.within(plant, self.drift):
                return Retune(Verdict.UNCHANGED, plant, residual)
        return Retune(Verdict.OFFERED, plant, residual)

    def accept[T](self, plant: Plant) -> T:
        """Record ``plant`` as the model in force and return its tuning.

        Call once the caller has committed to the retune, so the spacing clock
        and the drift baseline move together with what was actually applied.
        """
        self._applied = plant
        self._since = 0.0
        return self.rule(plant)

    def _diverging(self) -> bool:
        best = self._best_residual
        if best is None or not best:
            return False
        return abs(self.identifier.residual) > self.residual_growth * best
