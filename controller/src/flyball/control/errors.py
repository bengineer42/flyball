from flyball.core import (
    ConflictError,
    FlyballError,
    NotFoundError,
    NotReadyError,
)


class ControllerError(FlyballError):
    """Base for controller errors."""


class ControllerSuspendedError(ControllerError, ConflictError):
    """The controller is suspended."""

    def __init__(self) -> None:
        super().__init__("Controller is suspended.")


class LastReadingNotAvailableError(ControllerError, NotReadyError):
    """No reading has come round the loop yet. Not a fault; wait a tick."""

    def __init__(self) -> None:
        super().__init__("Controller has not seen a reading yet.")


class ControlLawNotRegisteredError(ControllerError, NotFoundError):
    """No control law registered under that name."""

    def __init__(self, tag: str) -> None:
        super().__init__(f"Control law '{tag}' is not registered.")


class ControlLawNotSetError(ControllerError, NotReadyError):
    """No control law is set."""

    def __init__(self) -> None:
        super().__init__("Control law is not set.")


class TuningNotRegisteredError(ControllerError, NotFoundError):
    def __init__(self, tuning: str) -> None:
        super().__init__(f"Control law tuning with name {tuning!r} is not registered.")


class ControllerNotStartedError(ControllerError, NotReadyError):
    """The controller has not been started."""

    def __init__(self) -> None:
        super().__init__("Controller has not been started.")
