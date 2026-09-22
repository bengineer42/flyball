"""The laws, feedforwards and generators that ship, plus what's left of `control`'s own concerns.

`Controller`/`ControlLaw`/`Feedforward`/`SetPointGenerator` (the Catalog/Config
machinery every one of these subclasses) moved to `flyball.model` with the
registry redesign (`brain/tasks/registry-redesign.md`); `Tuning`/`Tunings`
moved to `flyball.library.tunings` (`brain/tasks/engine-structure.md`, layer
4). What's left here is what ships built on top of that machinery: the 9
built-in laws, the plant-model feedforwards (`Affine`, `Table` -- `Setpoint`/
`NoFeedforward`, `Controller`'s own defaults, live in `flyball.model
.feedforward` instead, see that module's docstring), and the built-in
generators (`Hold`, `LinearRampSetpoint`, `Profile`).
"""

from .errors import (
    ControlLawNotRegisteredError,
    ControlLawNotSetError,
    ControllerSuspendedError,
    LastReadingNotAvailableError,
)
from .feedforward import Affine, Feedforward, FeedforwardConfig, Table
from .laws import (
    IMC,
    PI,
    PID,
    OnOff,
    OpenLoop,
    P,
    Scheduled,
    SlidingMode,
    SmithPredictor,
)
from .setpoint import (
    GeneratorConfig,
    Hold,
    LinearRampSetpoint,
    Profile,
    SetPointGenerator,
    SetPointGeneratorConfig,
)

__all__ = [
    "IMC",
    "PI",
    "PID",
    "Affine",
    "ControlLawNotRegisteredError",
    "ControlLawNotSetError",
    "ControllerSuspendedError",
    "Feedforward",
    "FeedforwardConfig",
    "GeneratorConfig",
    "Hold",
    "LastReadingNotAvailableError",
    "LinearRampSetpoint",
    "OnOff",
    "OpenLoop",
    "P",
    "Profile",
    "Scheduled",
    "SetPointGenerator",
    "SetPointGeneratorConfig",
    "SlidingMode",
    "SmithPredictor",
    "Table",
]
