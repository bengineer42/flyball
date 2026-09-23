# Controllers

Who drives what. A controller (a software control loop, not a device)
regulates one **measured** signal by writing one demand, its **output**.
It is keyed by the output's address, so a demand can have at most one.

The output must be a **demand** (`role` `demand`) that is **writable**
(`W`). A setting is never an output, however writable -- a range, a PWM
frequency, a configuration register changes how a device behaves, not what
it does to the process -- and neither is a demand only its device drives
(the humidity blender's `flows.*`, `[RP]`). Attaching one is refused, at
load or from the API, with "`'<address>' is a setting, not a demand: a
controller drives only demands`" (or "`… is not writable`"); the
controller form lists only writable demands. A generic adapter
(`scpi`, `modbus`, `i2c_table`, `qcodes`, `pymeasure`) makes every writable
entry a demand unless the entry says `role: setting`
([Drivers](devices/drivers.md#generic-instruments)).

```yaml
controllers:
  heaters.heater2:
    measured: furnace.zone2                                 # what is regulated
    law: { tag: PI, kp: 100, ki: 0.15, tt: 30 }
    feedforward: { tag: table, rate_gain: 3000, points: [[20, 0], [200, 289.4]] }
    default: true
```

| key | type | |
| --- | --- | --- |
| `measured` | address | the measured signal: a publishing signal, what is regulated (ISA's PV). Under `open_loop` it only sets the units and clocks the step |
| `law` | `{tag, …}` | `open_loop`; `P {kp}`; `PI {kp, ki, tt, b}`; `PID {kp, ki, kd, tt, b, n}` (`tt`: anti-windup tracking time, omitted or 0 disables it; `b`: setpoint weight; `n`: derivative filter, omitted leaves the derivative unfiltered); `IMC {gain, tau, dead_time, lam, derivative, n}`; `on_off {high, low, hysteresis}`; `smith {kp, ki, tt, gain, tau, dead_time, feedforward}`; `scheduled {points: [[setpoint, kp, ki, kd], …], tt, n}`; `sliding {k, lam, boundary}` — each in [Control laws](../3-extending/laws.md). Omit for none |
| `feedforward` | `{tag, …}` | `setpoint` (the measured unit passed through); `none`; `affine {gain, bias, rate_gain}`; `table {points, rate_gain}`. Omit: `setpoint` when the units agree, else `none` |
| `default` | bool | the controller a command means when it names none; at most one |
| `min_period_s` | number | step the law at most this often |

## Feedforward and units

A controller works in the measured signal's unit for the setpoint and the
output's for the output value; `feedforward` is what maps one to the other. `setpoint`
passes the setpoint through -- right when a controller drives a signal in
its own unit, as when `pwm_channel` has a `span` in °C. `affine` and
`table` are the static curve from measured to output (the power a furnace
zone needs to *hold* a temperature); `rate_gain` adds the extra needed to
*ramp* it. `none` is for an output with no static relationship to its
measured signal: the law does all the work from zero.

## Tunings

A law's gains may also live in a file under `tunings/` beside the rig
(`tunings/<tag>.yaml`, a `{tag, …gains}` document, loaded onto the rig at
start) and be chosen at run time: `PUT /api/tunings/{tag}`, or the tuning
picker on a faceplate. Autotune writes one:
[Tuning and autotune](../1-running/autotune.md). Writing a law of your own:
[Control laws](../3-extending/laws.md); the strict schema:
[the reference](../7-reference/rig-file.md#controllers).

## How a controller works

Enough to choose a law and a feedforward; the full mechanics are
[The controller in detail](../6-internals/controller.md).

### One tick

Every time a reading arrives on the controller's measured signal, it ticks.
First it checks the write would land. If the measured signal has gone stale
(`stale_after`), or a limit on the output follows a signal that has no
value yet (a supply humidity not read yet) or a non-finite one (NaN,
infinite), the controller is **held** and the tick stops here: the law
does not step, nothing is written, the output keeps what it last took,
and an event says why, once per hold (`stale_input`; `limit_unknown`,
then `limit_known` when writes resume). A held law cannot wind up, and
the first step after the hold counts as one ordinary interval, so the
time spent held is not integrated either.

Then:

1. **Resolve the setpoint** for this instant. The reference is either a
   fixed value or a *generator* — a function of time, such as a ramp —
   evaluated exactly at the reading's timestamp.
2. **Step the law.** The law takes `(elapsed, measured, setpoint)` and
   returns a **correction**: the offset to add to the setpoint.
3. **Form the output**: `output = feedforward(setpoint, rate) + correction`.
4. **Write it**: the controller calls `rig.write(output.node, {output:
   value}, by=self)`, which validates, clamps to `limits`, and commits;
   `expected` is what came back — `None` if the commit is deferred (a
   blocking device's writer thread) or the driver cannot say. An output
   with `max_rate` moves at most `max_rate` × one update period per write
   (its `poll_s`, else this controller's `min_period_s`, else 1 s), so the
   first write after a hold does not spend the allowance banked during it. The rig
   makes the same check on every write it is handed by a
   controller (a `regulate` while held is applied to the controller, not
   the output). A law that raises (no law set, say) is a `step_failed`
   event, once, and `step_recovered` when it steps again; the other
   controllers on the rig are not held up by it. A setpoint that is NaN or
   infinite is refused (422) before the controller changes.
5. **Remember** the measured reading, the output and what was delivered, so the next
   tick's anti-windup and any `attach_on_tick` observer can see them.

Nothing here knows what the quantity is. The controller works in the
measured unit for the setpoint and the output's unit for the output throughout;
the **feedforward** is what maps one to the other (`Setpoint`, the
identity, when the units agree; an `Affine` or `Table` curve; `NoFeedforward`
when there is none — an output with no static relationship to its measured signal).

### Why the law returns a correction

The law produces an *offset from the setpoint*, and the output starts from
the feedforward's own mapping of the setpoint. Two things follow:

- **Open loop is a law that returns zero** (`OpenLoop`), run under
  `REGULATING` — not to be confused with mode `OPEN` below, which instead
  holds whatever correction was last there rather than forcing it to zero.
  Either way the output ends up driven at (close to) the feedforward of
  the setpoint alone — how an experiment or a manual hold is run through
  the same path as regulation.
- **Handover is arithmetic.** Switching from manual to regulating, or
  swapping a tuning, means choosing what the correction should be at the
  instant of the switch — a `Transfer`: `none` (zero), `reset` (the law's
  own resume from the current reading), `carry` (keep the old correction),
  or `track` (match the output already being delivered, the default,
  bumpless). See [The loop in detail](../6-internals/controller.md#handover).

### Modes

| mode | what a tick does |
| --- | --- |
| `MANUAL` | nothing; the output is driven by demands directly |
| `OPEN` | write the feedforward against the held correction, law not stepped — an experiment or a manual hold run through the same path as regulation |
| `REGULATING` | step the law, then write |

Mode says what the controller is *doing*. Who is *allowed* to change it —
a program step, an operator, the API — is a separate question, answered
separately: `rig.write` refuses a manual write against a signal a
controller is driving, so mode also decides who may write.

### Time

A law never owns a clock. It is given `elapsed` — seconds since it was last
reset or resumed — and derives its own interval from the previous call. A
generator, by contrast, is a function of *absolute* time, because a ramp is
anchored to the instant it must land. That split is deliberate and is why
the two take different time arguments.

### `min_period_s`

A measured signal can update faster than a controller should step its law — an
oversampled sensor, a fast bus. `min_period_s` lets every reading update
`controller.measured` (so a client watching it always sees the latest
value) while the law only steps, and the output only changes, at that
minimum interval.

Next: [Programs](../1-running/programs/index.md).

