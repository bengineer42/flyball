# Identification

Autotuning is a one-off experiment. Identification is the same idea running
continuously: fit the plant from a controller's own samples, notice when it
has drifted, and offer a retune. `flyball.adaptive` is the slow half of a
self-tuning regulator.

## The model

ARX, first order with an input delay:

```
y[k] = a·y[k−1] + b·u[k−d] + Σ c[i]·w[i][k]
```

Chosen because it is **linear in its parameters**, which is what lets the
estimate recurse. `Arx.plant()` converts it to the continuous
`(gain, tau, dead_time)` the tuning rules take.

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
  excitation. Covariance bounding is the second line.
- **Dead time is not identifiable by recursion.** It comes from a calibration
  step test and is revisited rarely. Everything else tracks continuously.

## Using it

```python
from flyball.adaptive import Identifier, Sample, Schema, SelfTuner
from flyball.autotune import imc
from flyball.control import Transfer, ValueSource

schema = Schema(controlled=process, manipulated=demand, disturbances=(supply,))
identifier = Identifier(schema, interval=1.0, delay_samples=4)
tuner = SelfTuner(identifier, rule=imc)

# every tick
identifier.push(Sample(reading.value, controller.demand, (supply_reading.value,)))
tuner.observe(identifier.residual)
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
