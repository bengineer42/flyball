from humctrl.error import ConflictError, HumCtrlError, NotFoundError, UnachievableError
from humctrl.typing import Percent


class ControllerError(HumCtrlError):
    """Base class for exceptions raised by the controller."""


class ControllerSuspendedError(ControllerError, ConflictError):
    """Raised when an operation is attempted on a suspended controller."""

    def __init__(self) -> None:
        super().__init__("Controller is suspended.")


class ControlLawNotRegisteredError(ControllerError, NotFoundError):
    """Raised when a control law is not registered."""

    def __init__(self, tag: str) -> None:
        super().__init__(f"Control law '{tag}' is not registered.")


class HumidityRailError(ControllerError, UnachievableError):
    """The target humidity is outside the range the two lines can mix to.

    Reported on :class:`StreamState` rather than raised: the blend rails to the
    nearest achievable end and the run continues. No amount of pump capacity
    fixes it, so the remedy is a wetter or drier supply, not more flow.
    """

    def __init__(
        self, dry_humidity: Percent, wet_humidity: Percent, target_humidity: Percent
    ) -> None:
        super().__init__(
            f"Target humidity ({target_humidity}%) is outside the achievable range "
            f"{dry_humidity}% (dry) to {wet_humidity}% (wet)."
        )


class PumpHumiditiesError(ControllerError, UnachievableError):
    """The wet and dry line humidities are not in the expected order.

    Raised rather than reported: with no span between the lines there is no
    blend to compute, so the mixing model cannot produce an answer at all.
    """

    def __init__(self, wet: Percent, dry: Percent) -> None:
        super().__init__(
            f"Wet ({wet}) and dry ({dry}) humidities are not in the expected order. Wet humidity "
            "must be greater than dry humidity."
        )
