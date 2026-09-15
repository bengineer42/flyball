"""Identifying a plant while it runs, and retuning from what is found.

The slow half of a self-tuning regulator. :class:`Identifier` tracks a discrete
model of a loop from its own samples; :class:`SelfTuner` watches that model
drift and says when a retune is warranted. Neither writes to a loop -- the
caller derives gains and hands them over, so a retune goes through the same
bumpless path as any other tuning change and reports its own bump.

Generic in the same sense as :mod:`flyball.control`: a loop is a controlled
quantity, a manipulated one and any measured disturbances, named by
:class:`Schema`. Nothing here knows what is being controlled.

Sketched against a loop that already reads and commands::

    schema = Schema(controlled=process, manipulated=demand, disturbances=(supply,))
    identifier = Identifier(schema, interval=1.0, delay_samples=4)
    tuner = SelfTuner(identifier, rule=imc)

    # every tick
    identifier.push(Sample(reading.value, loop.demand, (supply_reading.value,)))
    tuner.observe(identifier.residual)
    tuner.elapsed(interval)

    # on a much slower clock
    retune = tuner.consider()
    if retune.offered:
        loop.regulate(ValueSource.SETPOINT, tuning=tuner.accept(retune.plant),
                      transfer=Transfer.TRACK)

Why this shape:

- The model is ARX because it is linear in its parameters, which is what lets
  the estimate recurse. The continuous form a tuning rule wants comes from
  :meth:`Arx.plant`.
- Updates are gated on excitation. A loop holding a setpoint says nothing about
  the plant, and an estimator that forgets will drift on noise while it waits.
- Dead time is not identifiable by recursion. It comes from a calibration step
  and is revisited rarely; everything else tracks continuously.
"""

from .errors import (
    AdaptiveError,
    ModelRejectedError,
    NotIdentifiedError,
    RegressorMismatchError,
)
from .identifier import Excitation, Identifier, Sample
from .rls import RecursiveLeastSquares
from .tuner import Bounds, Retune, SelfTuner, Verdict
from .types import Arx, Plant, Role, Schema, Term

__all__ = [
    "AdaptiveError",
    "Arx",
    "Bounds",
    "Excitation",
    "Identifier",
    "ModelRejectedError",
    "NotIdentifiedError",
    "Plant",
    "RecursiveLeastSquares",
    "RegressorMismatchError",
    "Retune",
    "Role",
    "Sample",
    "Schema",
    "SelfTuner",
    "Term",
    "Verdict",
]
