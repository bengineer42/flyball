from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from flyball.foundation import NonNegative, NormalisedPositive, Positive, PositiveInt

from .errors import NotIdentifiedError
from .rls import RecursiveLeastSquares
from .types import Arx, Plant, Schema


@dataclass(frozen=True, slots=True)
class Sample:
    """One observation of a loop, in the order its schema declares."""

    measured: float
    output: float
    disturbances: tuple[float, ...] = ()


class Excitation:
    """Whether the recent input has moved enough to learn from.

    A loop holding a setpoint says nothing about the plant, and a forgetting
    estimator drifts on noise while it waits; ramps and steps in a program
    supply the excitation. A response outlasts its cause -- a slow plant is
    still settling long after the demand stopped moving, and that settling is
    where the time constant shows -- so the gate stays open for `hold`
    samples after the input last moved.

    Args:
        window: How many recent inputs to judge on.
        threshold: The spread the window must show, in the input's units.
        hold: Samples the gate stays open after the input last moved; a few
            time constants' worth at the sample rate.
    """

    __slots__ = ("_since_moved", "_window", "hold", "threshold")

    def __init__(
        self, window: PositiveInt = 20, threshold: Positive = 0.5, hold: int = 100
    ) -> None:
        self._window: deque[float] = deque(maxlen=window)
        self.threshold = threshold
        self.hold = hold
        self._since_moved: int | None = None

    @property
    def spread(self) -> float:
        return max(self._window) - min(self._window) if self._window else 0.0

    def push(self, value: float) -> bool:
        """Add an input and say whether the loop is excited: moving now, or moved lately."""
        self._window.append(value)
        if self.spread >= self.threshold:
            self._since_moved = 0
            return True
        if self._since_moved is None:
            return False
        self._since_moved += 1
        return self._since_moved <= self.hold

    def reset(self) -> None:
        self._window.clear()
        self._since_moved = None


class Identifier:
    """Tracks a plant model from a loop's samples.

    Holds the delayed input history, gates updates on excitation, and converts
    the discrete estimate to the continuous form the tuning rules take.

    Args:
        schema: Which signals the model is built from, in order.
        interval: Nominal seconds between samples; only used to convert the
            discrete estimate.
        delay_samples: Input delay in samples. Not identifiable by recursion,
            so it comes from calibration.
        forgetting: Passed to the estimator. The default keeps about 200
            samples in view; a slow plant seen at a fast rate wants it nearer 1.
        excitation: The gate. Omit for the default window and hold.
        settle: Excited samples required before a model is offered.
    """

    __slots__ = (
        "_excitation",
        "_inputs",
        "_previous",
        "_rls",
        "_seen",
        "delay_samples",
        "interval",
        "residual",
        "schema",
        "settle",
    )

    def __init__(
        self,
        schema: Schema,
        interval: Positive = 1.0,
        delay_samples: int = 0,
        forgetting: NormalisedPositive = 0.995,
        excitation: Excitation | None = None,
        settle: PositiveInt = 100,
    ) -> None:
        self.schema = schema
        self.interval = interval
        self.delay_samples = delay_samples
        self.settle = settle
        self.residual: float = 0.0
        self._rls = RecursiveLeastSquares(schema.width, forgetting=forgetting)
        self._excitation = excitation or Excitation()
        self._inputs: deque[float] = deque(maxlen=delay_samples + 1)
        self._previous: float | None = None
        self._seen = 0

    @property
    def excited_samples(self) -> int:
        return self._seen

    @property
    def identified(self) -> bool:
        return self._seen >= self.settle

    @property
    def confidence(self) -> float:
        return self._rls.confidence

    def reset(self) -> None:
        """Forget the estimate, the history and the excitation window."""
        self._rls.reset()
        self._excitation.reset()
        self._inputs.clear()
        self._previous = None
        self._seen = 0
        self.residual = 0.0

    def push(self, sample: Sample) -> bool:
        """Fold one sample in; return whether it was used.

        The first sample, and any taken while the input is steady, are recorded
        but not fitted.
        """
        self._inputs.append(sample.output)
        excited = self._excitation.push(sample.output)
        previous, self._previous = self._previous, sample.measured

        if previous is None or not excited or len(self._inputs) <= self.delay_samples:
            return False

        regressor = [previous, self._inputs[0], 1.0, *sample.disturbances]
        self.residual = self._rls.update(regressor, sample.measured)
        self._seen += 1
        return True

    def arx(self) -> Arx:
        """The discrete estimate.

        Raises:
            NotIdentifiedError: Too few excited samples to offer a model.
        """
        if not self.identified:
            raise NotIdentifiedError(self._seen, self.settle)
        a, b, offset, *gains = self._rls.parameters
        return Arx(
            a=a,
            b=b,
            offset=offset,
            disturbance_gains=tuple(gains),
            interval=self.interval,
            delay_samples=self.delay_samples,
        )

    def plant(self) -> Plant:
        """The continuous estimate, for a tuning rule.

        Raises:
            NotIdentifiedError: Too few excited samples.
            ModelRejectedError: The estimate is not a stable first-order lag.
        """
        return self.arx().plant()

    def feedforward(self, setpoint: float, rate: NonNegative = 0.0) -> float:
        """The demand that would produce `setpoint` with no feedback.

        The steady-state term holds the setpoint; the `rate` term covers the
        lag while it moves.

        Raises:
            NotIdentifiedError: Too few excited samples.
            ModelRejectedError: The estimate is not a stable first-order lag.
        """
        plant = self.plant()
        if not plant.gain:
            return setpoint
        return (setpoint - plant.ambient + plant.tau * rate) / plant.gain
