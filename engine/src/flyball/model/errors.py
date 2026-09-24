from flyball.foundation import (
    ConflictError,
    FlyballError,
    NotFoundError,
    NotReadyError,
    UnachievableError,
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

    def __init__(self, name: str) -> None:
        super().__init__(f"Control law '{name}' is not registered.")


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


class FeedforwardNotInvertibleError(ControllerError, UnachievableError):
    """Asked for the setpoint behind a demand, but this feedforward has no inverse."""

    def __init__(self, type: str, reason: str | None = None) -> None:
        detail = f" ({reason})" if reason else ""
        super().__init__(f"Feedforward {type!r} cannot be inverted{detail}.")
