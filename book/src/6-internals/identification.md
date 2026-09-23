# Identification

Autotuning is a one-off experiment. Identification is the same idea running
continuously: fit the plant from a controller's own samples, notice when it
has drifted, and offer a retune. `flyball.adaptive` is the slow half of a
self-tuning regulator.

**Experimental**, not wired into any controller: `flyball.adaptive` is
imported only by its own tests (`tests/test_adaptive.py`), not by anything
that runs against a rig.

## The model

ARX, first order with an input delay and an operating point:

```
y[k] = a·y[k−1] + b·u[k−d] + offset + Σ c[i]·w[i][k]
```

Chosen because it is **linear in its parameters**, which is what lets the
estimate recurse. `Arx.plant()` converts it to the continuous
`(gain, tau, dead_time, ambient)` the tuning rules take.

The offset is what the plant rests at with no input, folded into one term
(`(1 − a)·ambient`). Without it a plant that does not rest at zero — a
chiller pulling below a warm room, a heater over ambient driven raw — cannot
be fitted at all: the fit trades the missing constant against `a` and `b`
and comes out with the wrong gain, sometimes the wrong sign. A loop whose
demand is already in the controlled quantity's units (the simulated rigs'
"smart" drives, where a demand of 50 °C holds 50 °C) has an offset near zero
and loses nothing by carrying the term.

A `Schema` names the inputs: one controlled signal, one manipulated, and any
measured disturbances. Nothing in the estimator knows what is being
controlled; a disturbance signal is what lets the model account for an
input the controller cannot command.

## The estimator

Recursive least squares with forgetting, in plain lists. For a handful of
terms, allocating an array per sample costs more than the arithmetic, and the
package stays dependency-free.

Two things make it survive a real rig:

- **Updates are gated on excitation.** A controller holding a setpoint says
  nothing about the plant, and an estimator that forgets drifts on noise
  while it waits. Ramps and setpoint steps in a program supply the
  excitation. The gate (`Excitation`) opens when the demand has moved by
  more than `threshold` within its `window` and **stays open for `hold`
  samples after it last moved**: a slow plant is still settling long after
  the demand stopped, and that settling is where the time constant shows.
  Size `hold` at a few time constants at the sample rate, and `threshold` to
  the demand's units — below the demand's own noise, every sample passes and
  the fit is of noise (the pole then lands outside `(0, 1)` and the model is
  refused rather than offered). Covariance bounding is the second line, and
  the default forgetting factor (0.995, about 200 samples in view) is the
  third: shorter memories wander on the near-collinear stretches of a slow
  response.
- **Dead time is not identifiable by recursion.** It comes from a calibration
  step test and is revisited rarely. Everything else tracks continuously.

## Using it

```python
from flyball.adaptive import Identifier, Sample, Schema, SelfTuner
from flyball.autotune import imc
from flyball.model.controller import ValueSource
from flyball.model.law import Transfer

schema = Schema(controlled=process, manipulated=demand, disturbances=(supply,))
identifier = Identifier(schema, interval=1.0, delay_samples=4)
tuner = SelfTuner(identifier, rule=imc)

# every tick
identifier.push(Sample(reading.value, controller.output, (supply_reading.value,)))
tuner.observe()
tuner.elapsed(interval)

# on a much slower clock
retune = tuner.consider()
if retune.offered:
    controller.regulate(
        ValueSource.SETPOINT, tuning=tuner.accept(retune.plant), transfer=Transfer.TRACK
    )
```

## Retuning is offered, never applied

`consider()` returns a verdict — `unidentified`, `implausible`, `unchanged`,
`too soon`, `diverging`, or `offered` — and the plant it was based on. The caller derives gains and hands them over, so a
retune takes the same bumpless path as any tuning change and reports its own
bump.

Retuning faster than the plant settles makes the two loops interact, which is
the usual way adaptive control goes unstable. `settling_periods` is a floor.

Two of the verdicts guard against a fit that is not the plant. *Implausible*
is a plant outside `Bounds`, or one whose gain has changed sign against the
model in force: a plant does not change the direction it responds in, so
that fit is of noise or of a loop that has not moved. *Diverging* compares a
running level of the prediction error (`tuner.observe()`, taken only on
ticks the identifier actually fitted, so a stale error between transients
does not pull the level down) against the best level seen since the model
in force was accepted; a level `residual_growth` times the best says the
model no longer describes the plant. One bad sample is not a verdict.

On the simulated rigs (`examples/simulated/{oven,tank}.yaml` and a
chiller-like lag, driven in closed loop by their own PI gains through four
setpoint steps) the identifier lands within 10 % of the oven's and the
tank's gain and time constant and within 25 % of the chiller's, sign
included; a plant whose gain halves mid-run is refitted and a retune
offered with about twice the controller gain; and IMC gains derived from the
fit settle the oven on a fresh step. `tests/test_adaptive.py` is the record.

## Feedforward

Inverting the model gives the demand that would produce a setpoint with no
feedback, plus a term for the rate the reference is moving at:
`identifier.feedforward(setpoint, rate)`. Because a reference is a function
of time, the controller can evaluate it ahead by the dead time. That is
two-degree-of-freedom control: feedforward shapes the response to known
setpoint changes, feedback handles disturbances and model error.

Feedforward amplifies model error in proportion, so gate it on the fit being
trusted, and put it inside the demand so the target's saturation still
reaches anti-windup.

!!! note "Status"
    The estimator and the retune policy exist and are generic. Wiring them
    into a controller — declaring a schema, feeding a sample per tick, taking
    a feedforward term — is not done.
