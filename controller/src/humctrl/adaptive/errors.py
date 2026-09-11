from humctrl.core import ConflictError, HumCtrlError, NotReadyError, UnachievableError


class AdaptiveError(HumCtrlError):
    """Base for everything humctrl.adaptive raises."""


class NotIdentifiedError(AdaptiveError, NotReadyError):
    """No model yet. The estimator has not seen enough excited samples."""

    def __init__(self, seen: int, needed: int) -> None:
        super().__init__(f"Model not identified: {seen} excited samples of {needed} needed.")


class ModelRejectedError(AdaptiveError, UnachievableError):
    """The fit produced parameters that cannot describe a stable first-order plant."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Model rejected: {reason}")


class RegressorMismatchError(AdaptiveError, ConflictError):
    """A sample carried a different number of terms than the estimator was built for."""

    def __init__(self, expected: int, got: int) -> None:
        super().__init__(f"Estimator takes {expected} terms, sample carried {got}.")
