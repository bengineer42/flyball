# Controllers

Who drives what. Keyed by the **target** -- the writable signal being
driven -- so a signal can have at most one.

```yaml
controllers:
  heaters.heater2:
    signal: furnace.zone2                                 # the source: what is regulated
    law: { tag: PI, kp: 100, ki: 0.15, tt: 30 }
    feedforward: { tag: table, rate_gain: 3000, points: [[20, 0], [200, 289.4]] }
    default: true
```

| key | type | |
| --- | --- | --- |
| `signal` | address | the source, a publishing signal |
| `law` | `{tag, …}` | `open_loop`; `P {kp}`; `PI {kp, ki, tt, b}`; `PID {kp, ki, kd, tt, b, n}` (`tt`: anti-windup tracking time, omitted or 0 disables it; `b`: setpoint weight; `n`: derivative filter, omitted leaves the derivative unfiltered); `IMC {gain, tau, dead_time, lam, derivative, n}`; `on_off {high, low, hysteresis}`; `smith {kp, ki, tt, gain, tau, dead_time, feedforward}`; `scheduled {points: [[setpoint, kp, ki, kd], …], tt, n}`; `sliding {k, lam, boundary}` — each in [Control laws](../3-extending/laws.md). Omit for none |
| `feedforward` | `{tag, …}` | `setpoint` (the source's unit passed through); `none`; `affine {gain, bias, rate_gain}`; `table {points, rate_gain}`. Omit: `setpoint` when the units agree, else `none` |
| `default` | bool | the controller a command means when it names none; at most one |
| `min_period_s` | number | step the law at most this often |

## Feedforward and units

A controller works in the source's unit for the setpoint and the target's
for the demand; `feedforward` is what maps one to the other. `setpoint`
passes the setpoint through -- right when a controller drives a signal in
its own unit, as when `pwm_channel` has a `span` in °C. `affine` and
`table` are the static curve from source to target (the power a furnace
zone needs to *hold* a temperature); `rate_gain` adds the extra needed to
*ramp* it. `none` is for a target with no static relationship to its
source: the law does all the work from zero.

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

Every time a reading arrives on the controller's source, it ticks:

1. **Resolve the setpoint** for this instant. The reference is either a
   fixed value or a *generator* — a function of time, such as a ramp —
   evaluated exactly at the reading's timestamp.
2. **Step the law.** The law takes `(elapsed, reading, setpoint)` and
   returns a **correction**: the offset to add to the setpoint.
3. **Form the demand**: `demand = feedforward(setpoint, rate) + correction`.
4. **Write it**: the controller calls `rig.demand(target.node, {target:
   demand}, by=self)`, which validates, clamps to `limits`, and commits;
   `expected` is what came back — `None` if the commit is deferred (a
   blocking device's writer thread) or the driver cannot say. If a limit
   follows a signal that has no value yet (a supply humidity not read
   yet) or a non-finite one (NaN, infinite), or the source has gone stale (`stale_after`), the write is
   **held**: nothing is applied, `expected` is `None`, and an event says
   why (`limit_unknown` once, then `limit_known` when writes resume;
   `stale_input`). A law that raises (no law set, say) is a `step_failed`
   event, once, and `step_recovered` when it steps again; the other
   controllers on the rig are not held up by it. A setpoint that is NaN or
   infinite is refused (422) before the controller changes.
5. **Remember** the reading, the demand and what was delivered, so the next
   tick's anti-windup and any `attach_on_tick` observer can see them.

Nothing here knows what the quantity is. The controller works in the
source's unit for the setpoint and the target's for the demand throughout;
the **feedforward** is what maps one to the other (`Setpoint`, the
identity, when the units agree; an `Affine` or `Table` curve; `NoFeedforward`
when there is none — a target with no static relationship to its source).

### Why a correction, not an output

The law produces an *offset from the setpoint*, and the demand starts from
the feedforward's own mapping of the setpoint. Two things follow:

- **Open loop is a law that returns zero** (`OpenLoop`), run under
  `REGULATING` — not to be confused with mode `OPEN` below, which instead
  holds whatever correction was last there rather than forcing it to zero.
  Either way the target ends up driven at (close to) the feedforward of
  the setpoint alone — how an experiment or a manual hold is run through
  the same path as regulation.
- **Handover is arithmetic.** Switching from manual to regulating, or
  swapping a tuning, means choosing what the correction should be at the
  instant of the switch — a `Transfer`: `none` (zero), `reset` (the law's
  own resume from the current reading), `carry` (keep the old correction),
  or `track` (match the demand already being delivered, the default,
  bumpless). See [The loop in detail](../6-internals/controller.md#handover).

### Modes

| mode | what a tick does |
| --- | --- |
| `MANUAL` | nothing; the target is driven by demands directly |
| `OPEN` | write the feedforward against the held correction, law not stepped — an experiment or a manual hold run through the same path as regulation |
| `REGULATING` | step the law, then write |

Mode says what the controller is *doing*. Who is *allowed* to change it —
a program step, an operator, the API — is a separate question, answered
separately: `rig.demand` refuses a manual write against a signal a
controller is driving, so mode also decides who may write.

### Time

A law never owns a clock. It is given `elapsed` — seconds since it was last
reset or resumed — and derives its own interval from the previous call. A
generator, by contrast, is a function of *absolute* time, because a ramp is
anchored to the instant it must land. That split is deliberate and is why
the two take different time arguments.

### `min_period_s`

A source can update faster than a controller should step its law — an
oversampled sensor, a fast bus. `min_period_s` lets every reading update
`controller.reading` (so a client watching it always sees the latest
value) while the law only steps, and the demand only changes, at that
minimum interval.

Next: [Programs](../1-running/programs/index.md).

