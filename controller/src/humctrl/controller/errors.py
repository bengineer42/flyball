from humctrl.error import (
    ConflictError,
    HumCtrlError,
    NotFoundError,
    NotReadyError,
)


class ControllerError(HumCtrlError):
    """Base class for exceptions raised by the controller."""


class ControllerSuspendedError(ControllerError, ConflictError):
    """Raised when an operation is attempted on a suspended controller."""

    def __init__(self) -> None:
        super().__init__("Controller is suspended.")


class LastReadingNotAvailableError(ControllerError, NotReadyError):
    """The controller has not been given a reading yet.

    Not a fault: nothing has come round the loop so far. Anything anchored to
    the process value has to wait a tick, whereas anchoring to the setpoint
    never needs a sensor at all.
    """

    def __init__(self) -> None:
        super().__init__("Controller has not seen a reading yet.")


class ControlLawNotRegisteredError(ControllerError, NotFoundError):
    """Raised when a control law is not registered."""

    def __init__(self, tag: str) -> None:
        super().__init__(f"Control law '{tag}' is not registered.")
