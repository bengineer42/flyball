"""Re-exported from `flyball.model.errors`, their canonical home.

Moved there with the type/law/controller/feedforward split (they're needed by
`model/law.py`, `model/controller.py` and `model/feedforward.py` themselves,
so they can't stay behind in `control/` without a circular import). Kept
importable from here too, since `control/laws.py` and friends still raise
some of them and nothing about the error taxonomy itself changed.
"""

from flyball.model.errors import (
    ControlLawNotRegisteredError,
    ControlLawNotSetError,
    ControllerError,
    ControllerNotStartedError,
    ControllerSuspendedError,
    FeedforwardNotInvertibleError,
    LastReadingNotAvailableError,
    TuningNotRegisteredError,
)

__all__ = [
    "ControlLawNotRegisteredError",
    "ControlLawNotSetError",
    "ControllerError",
    "ControllerNotStartedError",
    "ControllerSuspendedError",
    "FeedforwardNotInvertibleError",
    "LastReadingNotAvailableError",
    "TuningNotRegisteredError",
]
