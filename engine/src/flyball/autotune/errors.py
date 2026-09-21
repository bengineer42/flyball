from flyball.foundation.errors import ConflictError, FlyballError, NotReadyError, UnachievableError


class AutotuneError(FlyballError):
    """Base class for exceptions raised by the autotuner."""


class ExperimentTimeoutError(AutotuneError, ConflictError):
    """A phase of an experiment ran out of time.

    Usually the rig: no flow, a band tighter than the noise, or a slow chamber.
    """

    def __init__(self, phase: str, timeout: float) -> None:
        super().__init__(f"Autotune phase '{phase}' did not finish within {timeout}s.")


class ExperimentIncompleteError(AutotuneError, NotReadyError):
    """The result was read before the experiment produced one."""

    def __init__(self, experiment: str) -> None:
        super().__init__(f"{experiment} has not finished; there is no result yet.")


class ResponseTooSmallError(AutotuneError, UnachievableError):
    """The reading moved too little to identify from. Step further, or narrow the hysteresis."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Response too small to fit: {detail}")


class NoDeadTimeError(AutotuneError, UnachievableError):
    """A rule that divides by the dead time was handed a model without any."""

    def __init__(self, rule: str) -> None:
        super().__init__(f"The {rule} rule needs a plant with dead time; this model has none.")
