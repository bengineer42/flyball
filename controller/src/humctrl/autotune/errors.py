from humctrl.core.errors import ConflictError, HumCtrlError, NotReadyError, UnachievableError


class AutotuneError(HumCtrlError):
    """Base class for exceptions raised by the autotuner."""


class ExperimentTimeoutError(AutotuneError, ConflictError):
    """A phase of an experiment ran out of time.

    Nearly always the rig, not the tuner: a pump that is not moving air, a
    settling band tighter than the sensor noise, or a chamber far slower than
    the timeout allows.
    """

    def __init__(self, phase: str, timeout: float) -> None:
        super().__init__(f"Autotune phase '{phase}' did not finish within {timeout}s.")


class ExperimentIncompleteError(AutotuneError, NotReadyError):
    """The result was read before the experiment produced one."""

    def __init__(self, experiment: str) -> None:
        super().__init__(f"{experiment} has not finished; there is no result yet.")


class ResponseTooSmallError(AutotuneError, UnachievableError):
    """The reading moved too little to identify anything from.

    A step that lands inside the noise, or a relay whose oscillation never
    escapes its own dead band, carries no information about the plant. Step
    further, or narrow the hysteresis.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"Response too small to fit: {detail}")


class NoDeadTimeError(AutotuneError, UnachievableError):
    """A rule that divides by the dead time was handed a model without any."""

    def __init__(self, rule: str) -> None:
        super().__init__(f"The {rule} rule needs a plant with dead time; this model has none.")
