from __future__ import annotations

from flyball.core import NormalisedPositive, Positive

from .errors import RegressorMismatchError

type Vector = list[float]
type Matrix = list[list[float]]


def identity(width: int, scale: float) -> Matrix:
    """A `width`-square diagonal matrix, for seeding a covariance."""
    return [[scale if row == column else 0.0 for column in range(width)] for row in range(width)]


def trace(matrix: Matrix) -> float:
    return sum(matrix[index][index] for index in range(len(matrix)))


class RecursiveLeastSquares:
    """Tracks the parameters of a model that is linear in them.

    Plain lists, not arrays: regressors are a few terms wide and the estimator
    stays dependency-free.

    Args:
        width: How many parameters; every regressor must match.
        forgetting: How fast old samples lose weight. One never adapts; lower
            tracks drift faster and follows noise further.
        covariance: Initial diagonal. Large means "no idea yet".
        covariance_limit: Trace above which the covariance is rescaled, so it
            cannot grow without bound during a lull in excitation.
    """

    __slots__ = ("_covariance", "_limit", "_parameters", "forgetting", "width")

    def __init__(
        self,
        width: int,
        forgetting: NormalisedPositive = 0.98,
        covariance: Positive = 1_000.0,
        covariance_limit: Positive = 1e8,
    ) -> None:
        self.width = width
        self.forgetting = forgetting
        self._limit = covariance_limit
        self._parameters: Vector = [0.0] * width
        self._covariance: Matrix = identity(width, covariance)

    @property
    def parameters(self) -> tuple[float, ...]:
        return tuple(self._parameters)

    @property
    def confidence(self) -> float:
        """Falls as the covariance shrinks. Zero means nothing learned yet."""
        total = trace(self._covariance)
        return 0.0 if total <= 0.0 else 1.0 / (1.0 + total)

    def reset(self, covariance: Positive = 1_000.0) -> None:
        """Forget the estimate and start again wide open."""
        self._parameters = [0.0] * self.width
        self._covariance = identity(self.width, covariance)

    def predict(self, regressor: Vector) -> float:
        """What the current parameters say the output will be."""
        return sum(
            term * parameter for term, parameter in zip(regressor, self._parameters, strict=True)
        )

    def update(self, regressor: Vector, output: float) -> float:
        """Fold one sample in; return the prediction error before the update.

        Raises:
            RegressorMismatchError: The regressor is the wrong width.
        """
        if len(regressor) != self.width:
            raise RegressorMismatchError(self.width, len(regressor))

        # P @ phi, then the scalar denominator, then the gain -- one pass each,
        # so a k-term model costs O(k^2) multiplies and no allocation beyond the
        # two vectors below.
        covariance = self._covariance
        scaled = [
            sum(row[column] * regressor[column] for column in range(self.width))
            for row in covariance
        ]
        denominator = self.forgetting + sum(
            regressor[index] * scaled[index] for index in range(self.width)
        )
        gain = [value / denominator for value in scaled]

        residual = output - self.predict(regressor)
        for index in range(self.width):
            self._parameters[index] += gain[index] * residual

        for row in range(self.width):
            covariance_row = covariance[row]
            for column in range(self.width):
                covariance_row[column] = (
                    covariance_row[column] - gain[row] * scaled[column]
                ) / self.forgetting

        self._bound_covariance()
        return residual

    def _bound_covariance(self) -> None:
        """Rescale if the covariance has passed its limit, keeping its shape."""
        total = trace(self._covariance)
        if total <= self._limit:
            return
        scale = self._limit / total
        for row in self._covariance:
            for column in range(self.width):
                row[column] *= scale
