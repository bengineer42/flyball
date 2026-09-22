# Autotune

Working out the gains instead of guessing them. Three steps, each usable on
its own:

!!! tip "On a running rig"
    You do not have to drive any of this yourself: the `tune` step below does
    the three in one, as a program step or as a single command.

1. **Measure.** A `StepTest` or a `RelayTest`, driven from whatever
   controller already reads the sensor.
2. **Model.** A step test yields an `FOPDT` — gain, time constant, dead time.
   A relay test yields an `Ultimate` — the gain and period at which the loop
   just oscillates — directly.
3. **Tune.** A rule turns either into `Gains`, which carry a law config and a
   tuning to register.

Nothing here writes to the rig. The caller drives the experiment, so a run is
as interruptible as the controller driving it.

## The `tune` step

`tune` is an ordinary program command, so the same thing is a step in a
program and a one-shot you can fire at a running rig. It settles the loop,
steps it open-loop, fits the response and stores the gains under `save_as`,
where a following `regulate` names them:

```yaml
- tune: {loop: heaters.heater2, save_as: zone2}
- regulate: {loop: heaters.heater2, setpoint: 300, tuning: zone2}
```

Everything but the loop has a default worked out from the rig: the step is a
tenth of the source signal's range directed away from whichever end is
nearer, the base is the current reading, and the band is a twentieth of the
step. Give `size` and `band` yourself when the defaults do not suit the
signal -- a reading whose declared range is far wider than its working span
is the usual reason.

| argument | choose it |
| --- | --- |
| `save_as` | the tuning name a later `regulate` will use; default `fitted` |
| `size` | well above the noise, within the range the controller will work over |
| `window` | longer than the dead time, or the flat stretch before the response reads as a plateau |
| `band` | above the sensor noise, well below `size` |
| `rule` | `imc` (one dial, `lam`) or `amigo` (bounded sensitivity, PID only) |
| `derivative` | off by default; PI gives up little on a noisy reading |
| `timeout` | seconds per plateau; omitted waits for ever |

One loop per step, never a list: two experiments at once on a shared plant
contaminate each other's responses. Three furnace zones are three `tune:`
steps.

As a one-shot it is the same command through `POST /api/programs/command`, so
it runs on the programmer's thread and `POST /api/programs/interrupt` stops
it. **An interrupted tune puts the loop back the way it found it** -- the
previous law, mode and setpoint -- rather than leaving it open-loop at a
stepped target.

The fitted tuning lands on the running rig, not in the store. Promote one you
want to keep with `PUT /api/history/tunings/{name}`, which versions it.

## Set up

The rest of this page is the same three steps by hand, for a rig you are
driving from Python rather than from a program.

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
