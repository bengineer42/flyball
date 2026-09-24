"""Identifying a plant while it runs, and retuning from what is found.

Experimental: not wired into any controller or rig. Nothing in `flyball`
outside this package and its own tests (`tests/test_adaptive.py`) imports it.

[Identifier][flyball.adaptive.Identifier] tracks a discrete model of a loop
from its samples; [SelfTuner][flyball.adaptive.SelfTuner] watches it drift and
says when a retune is warranted. Neither writes to a loop: the caller hands
the gains over, so a retune takes the same bumpless path as any tuning change.

A loop is a measured quantity, the output it writes and any measured
disturbances, named by [Schema][flyball.adaptive.Schema]; nothing here knows
what is regulated.

    schema = Schema(measured=process, output=demand, disturbances=(supply,))
    identifier = Identifier(schema, interval=1.0, delay_samples=4)
    tuner = SelfTuner(identifier, rule=imc)

    # every tick
    identifier.push(Sample(reading.value, loop.output_value, (supply_reading.value,)))
    tuner.observe()
    tuner.elapsed(interval)

    # on a much slower clock
    retune = tuner.consider()
    if retune.offered:
        loop.regulate(ValueSource.SETPOINT, tuning=tuner.accept(retune.plant),
                      transfer=Transfer.TRACK)

The model is ARX because it is linear in its parameters, so the estimate can
recurse; [Arx.plant][flyball.adaptive.types.Arx.plant] gives the continuous
form, operating point included, so a plant that rests away from zero (a
chiller against a warm room) fits as well as one whose demand is already in
the measured quantity's units. Updates are gated on excitation, and the
gate stays open for a while after the demand last moved, since the response
to a step is where a slow plant's time constant shows. Dead time is not
identifiable by recursion, so it comes from calibration.
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
from .types import Arx, ModelTerm, Plant, Schema, Term

__all__ = [
    "AdaptiveError",
    "Arx",
    "Bounds",
    "Excitation",
    "Identifier",
    "ModelRejectedError",
    "ModelTerm",
    "NotIdentifiedError",
    "Plant",
    "RecursiveLeastSquares",
    "RegressorMismatchError",
    "Retune",
    "Sample",
    "Schema",
    "SelfTuner",
    "Term",
    "Verdict",
]
