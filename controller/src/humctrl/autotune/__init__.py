"""Working out the gains instead of guessing them.

Three steps, each usable on its own:

1. **Measure.** :class:`StepTest` or :class:`RelayTest`, driven from whatever
   loop already reads the sensor. Both are state machines: push
   ``(time, reading)``, command the target they return, stop when ``done``.
2. **Model.** A step test yields an :class:`FOPDT` — gain, time constant, dead
   time. A relay test skips this and yields an :class:`Ultimate` directly.
3. **Tune.** A rule turns either into :class:`Gains`, which carry a
   :attr:`~Gains.config` ready for a controller and a
   :meth:`~Gains.to_tuning` to register.

Run the experiment through the feedforward path, with the law set to
:class:`~humctrl.controller.OpenLoop`, so the plant gain the tuner sees is the
one the trim loop will see. See :mod:`humctrl.autotune.experiments`.

Sketched against a loop that already has a clock and a reader::

    test = StepTest(base=50.0, size=10.0, window=120.0, band=0.3, timeout=1800.0)
    while not test.done:
        reading = read()
        controller.set_stream(humidity=test.step(reading.time, reading.humidity))
        controller.apply()

    model = test.result
    gains = imc(model)                    # or imc(model, lam=model.tau / 2) to push it
    manager.register_tuning("fitted", gains.to_tuning("fitted"))

Nothing here writes to the rig or mutates a controller; the caller does both, so
an autotune run is as interruptible as the loop driving it.
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
