"""Working out the gains instead of guessing them.

1. **Measure.** [StepTest][flyball.autotune.StepTest] or
   [RelayTest][flyball.autotune.RelayTest]: push `(time, reading)`, command
   the target returned, stop when `done`.
2. **Model.** A step test yields an [FOPDT][flyball.autotune.FOPDT]; a relay
   test yields an [Ultimate][flyball.autotune.Ultimate] directly.
3. **Tune.** A rule turns either into [Gains][flyball.autotune.Gains], with a
   [config][flyball.autotune.types.Gains.config] for a controller and
   [to_tuning][flyball.autotune.types.Gains.to_tuning] to register.

Run the experiment with the law set to
[OpenLoop][flyball.control.laws.OpenLoop] so the gain measured is the one the
trim loop will see; see [flyball.autotune.experiments][].

    test = StepTest(base=50.0, size=10.0, window=120.0, band=0.3, timeout=1800.0)
    while not test.done:
        reading = read()
        controller.set_stream(humidity=test.step(reading.time, reading.humidity))
        controller.apply()

    gains = imc(test.result)
    manager.register_tuning("fitted", gains.to_tuning("fitted"))

Nothing here writes to the rig or a controller, so a run is as interruptible
as the loop driving it.
"""

from .errors import (
    AutotuneError,
    ExperimentIncompleteError,
    ExperimentTimeoutError,
    NoDeadTimeError,
    ResponseTooSmallError,
)
from .experiments import RelayTest, StepTest
from .fit import SteadyState, fit_fopdt
from .rules import amigo, imc, tyreus_luyben, ziegler_nichols
from .types import FOPDT, Gains, Sample, Ultimate

__all__ = [
    "FOPDT",
    "AutotuneError",
    "ExperimentIncompleteError",
    "ExperimentTimeoutError",
    "Gains",
    "NoDeadTimeError",
    "RelayTest",
    "ResponseTooSmallError",
    "Sample",
    "SteadyState",
    "StepTest",
    "Ultimate",
    "amigo",
    "fit_fopdt",
    "imc",
    "tyreus_luyben",
    "ziegler_nichols",
]
