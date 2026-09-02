from humctrl.typing import Percent


class ControllerError(Exception):
    """Base class for exceptions raised by the controller."""


class HumidityRailError(ControllerError, ValueError):
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


class PumpHumiditiesError(ControllerError, ValueError):
    """The wet and dry line humidities are not in the expected order.

    Raised rather than reported: with no span between the lines there is no
    blend to compute, so the mixing model cannot produce an answer at all.
    """

    def __init__(self, wet: Percent, dry: Percent) -> None:
        super().__init__(
            f"Wet ({wet}) and dry ({dry}) humidities are not in the expected order. Wet humidity "
            "must be greater than dry humidity."
        )
