# Autotune

Working out the gains instead of guessing them. Three steps, each usable on
its own:

1. **Measure.** A `StepTest` or a `RelayTest`, driven from whatever
   controller already reads the sensor.
2. **Model.** A step test yields an `FOPDT` — gain, time constant, dead time.
   A relay test yields an `Ultimate` — the gain and period at which the loop
   just oscillates — directly.
3. **Tune.** A rule turns either into `Gains`, which carry a law config and a
   tuning to register.

Nothing here writes to the rig. The caller drives the experiment, so a run is
as interruptible as the controller driving it.

## Set up

Run an experiment with the law set to `OpenLoop` and the target moved
directly. The step then passes through the device's own arithmetic, so the
gain measured is the one the trim controller will see.

```python
from flyball.autotune import RelayTest, StepTest, imc
from flyball.control import OpenLoop, ValueSource

controller.regulate(50.0, tuning=OpenLoop.config())
```

## Step test

Hold, step, hold. Fits a model to the response between the plateaus. The
gentler experiment: the rig only moves between two steady targets.

```python
test = StepTest(base=50.0, size=10.0, window=120.0, band=0.3, timeout=1800.0)
while not test.done:
    reading = ...                                  # the latest on the controller's source signal
    target = test.step(reading.time_ns / 1e9, reading.value)
    controller.set_reference(target)
model = test.result                                # an FOPDT
```

| argument | choose it |
| --- | --- |
| `size` | well above the noise, within the range the controller will work over |
| `window` | longer than the dead time, or the flat stretch before the response reads as a plateau |
| `band` | above the sensor noise, well below `size` |
| `timeout` | per plateau; `None` waits forever |

`model.normalised_dead_time` (`θ/(θ+τ)`) says how hard the plant is: below
0.2 most tunings work; above 0.6 no PID does well.

## Relay test

Bang-bang the target and read the critical point off the limit cycle. No
model in between, at the cost of deliberately cycling the rig.

```python
test = RelayTest(centre=50.0, amplitude=5.0, hysteresis=0.4, cycles=4, timeout=1800.0)
```

`hysteresis` stops the relay chattering on noise; set it just above the
peak-to-peak noise. Expect the measured gain to come out 10–20 % low, which
errs towards detuning.

## Rules

| rule | takes | character |
| --- | --- | --- |
| `imc(model, lam=…)` | FOPDT | one dial, `lam`, the closed-loop time constant in seconds; the preferred rule |
| `amigo(model)` | FOPDT | bounded sensitivity; PID only |
| `tyreus_luyben(ultimate)` | Ultimate | Ziegler–Nichols detuned; the default if a relay test is all there is |
| `ziegler_nichols(ultimate)` | Ultimate | the baseline everything is compared to; not a good default |

`imc` defaults `lam` to about as fast as the plant already is. Halve it to
push harder; below the dead time it gains nothing.

## Apply

```python
gains = imc(model)
rig.tunings.add(gains.to_tuning("fitted"))
controller.regulate(ValueSource.SETPOINT, tuning=rig.tunings.get("fitted"))   # bumpless retune
```

Or over HTTP: `PUT /api/tunings/fitted` with the law config, then a
`regulate` step naming it.
