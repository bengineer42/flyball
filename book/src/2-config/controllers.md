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
    law: { type: pi, kp: 100, ki: 0.15, tt_s: 30 }
    feedforward: { type: table, rate_gain: 3000, points: [[20, 0], [200, 289.4]] }
    is_default: true
    on_fault: { freeze_s: 30, then: stop }                  # frozen 30 s of fault time, then its stop
```

| key | type | |
| --- | --- | --- |
| `measured` | address | the measured signal: a published signal, what is regulated (ISA's PV). Under `open_loop` it only sets the units and clocks the step |
| `law` | `{type, …}` | `open_loop`; `P {kp}`; `PI {kp, ki, tt_s, b}`; `PID {kp, ki, kd, tt_s, b, n}` (`tt_s`: anti-windup tracking time, omitted or 0 disables it; `b`: setpoint weight; `n`: derivative filter, omitted leaves the derivative unfiltered); `IMC {gain, tau_s, dead_time_s, lam_s, derivative, n}`; `on_off {high, low, hysteresis}`; `smith {kp, ki, tt_s, gain, tau_s, dead_time_s, feedforward}`; `scheduled {points: [[setpoint, kp, ki, kd], …], tt_s, n}`; `sliding {k, lam, boundary}` — each in [Control laws](../3-extending/laws.md). Omit for none |
| `feedforward` | `{type, …}` | `identity` (the setpoint passed through, in the measured unit); `none`; `affine {gain, bias, rate_gain}`; `table {points, rate_gain}`. Omit: `identity` when the units agree, else `none` |
| `is_default` | bool | the controller a command means when it names none; at most one |
| `min_period_s` | number | update the law at most this often |
| `on_fault` | `freeze` \| `manual` \| `stop` \| `stop_device` \| `{freeze_s, then}` | what the controller does once its measured signal has been faulty long enough; default `freeze`. [Below](#on_fault-what-a-controller-does-about-a-faulty-source) |
| `setpoint_period_s` | number | while following a moving setpoint (a ramp, a profile), re-apply its feedforward this often between readings, [below](#a-setpoint-that-moves-faster-than-its-sensor). Above zero; unset: `max(0.1 s, poll_s / 4)` from the measured signal's `poll_s` (1 s for a pushed one) |

## Feedforward and units

A controller works in the measured signal's unit for the setpoint and the
output's for the output value; `feedforward` is what maps one to the other. `identity`
passes the setpoint through -- right when a controller drives a signal in
its own unit, as when `pwm_channel` has a `span` in °C. `affine` and
`table` are the static curve from measured to output (the power a furnace
zone needs to *hold* a temperature); `rate_gain` adds the extra needed to
*ramp* it. `none` is for an output with no static relationship to its
measured signal: the law does all the work from zero.

## Tunings

A law's gains may also live in a file under `tunings/` beside the rig
(`tunings/<name>.yaml`, a `{type, …gains}` document, loaded onto the rig at
start) and be chosen at run time: `PUT /api/tunings/{name}`, or the tuning
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
(`stale_after_s`), or a limit on the output follows a signal that has no
value yet (a supply humidity not read yet) or a non-finite one (NaN,
infinite), the controller is **held** and the tick stops here: the law
does not step, nothing is written, the output keeps what it last took,
and a condition on the controller says why while it lasts (`stale_input`
or `limit_unknown`: one `raised` event on entering the hold, one
`cleared` when writes resume, nothing per tick between). A held law cannot wind up, and
the first step after the hold counts as one ordinary interval, so the
time spent held is not integrated either.

A measured reading with **no value** (`invalid`, `stale`,
`not_applicable`: [no value](../4-server/wire.md#a-reading-with-no-value))
holds it the same way, with the condition `frozen` (`info` for a benign
`not_applicable`, `warning` for a fault; `details: {signal, quality,
reason}`). The law is never called with a missing value, and no number
stands in for one. It steps again, bumplessly, once 3 readings in a row
have a value. `regulate` from `measured` is refused while the newest
reading has none; any other `at` still regulates, and the law is seeded
as if nothing had been read (`cold`).

A measured signal that stops arriving goes `stale` at its threshold
(`stale_after_s`, default `max(3 × poll_s, 5 s)`:
[liveness](devices/index.md#liveness-a-signal-that-stops-arriving)), pushed
by the rig on its own clock, so the controller freezes then even though no
reading comes. Until then it holds its last output: for a heater, or any
loop whose held output can do harm, keep the measured signal's `poll_s` well
inside the plant's time to harm, and set `stale_after_s` near
`2 × poll_s` (lower lets one late read declare it stale).

**Fault time.** While the measured signal is a fault (`invalid`, `stale`
for any reason), the rig accrues fault time on its clock; a reading with a
value pauses it without resetting it, and 3 in a row end the outage. When
the accrued time reaches the wait -- `max(2 × poll_s, 1 s)` for a single
bad observation (`invalid`, `stale(device_offline | device_hung |
write_failed)`), none for staleness by age, none for a law that raises --
the outage is *released*, once, by a timer on the rig clock: a source that
delivers nothing is released all the same. What a release does is the
controller's [`on_fault`](#on_fault-what-a-controller-does-about-a-faulty-source).
A benign `not_applicable` or `pending` accrues nothing and never releases.

Then:

1. **Resolve the setpoint** for this instant. The reference is either a
   fixed value or a *generator* — a function of time, such as a ramp —
   evaluated exactly at the reading's timestamp.
2. **Update the law.** The law takes `(elapsed, measured, setpoint)` and
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
   condition on the controller, raised once and cleared when it steps again; the other
   controllers on the rig are not held up by it. A setpoint that is NaN or
   infinite is refused (422) before the controller changes.
5. **Remember** the measured reading, the output and what was delivered, so the next
   tick's anti-windup and any `attach_on_tick` observer can see them.

Nothing here knows what the quantity is. The controller works in the
measured unit for the setpoint and the output's unit for the output throughout;
the **feedforward** is what maps one to the other (`Setpoint`, the
identity, when the units agree; an `Affine` or `Table` curve; `NoFeedforward`
when there is none — an output with no static relationship to its measured signal).

### A setpoint that moves faster than its sensor

A reading arrives every `poll_s`, and the setpoint is sampled at each. For a
ramp or a profile, that would move the output in `poll_s` steps even where
the feedforward alone could follow the setpoint smoothly. So while a
controller is `REGULATING` on a generator that has not finished, through a
feedforward (not `none`), the rig re-applies `feedforward(setpoint,
rate) + correction` every `setpoint_period_s` between readings. The law
steps only on real readings: nothing is integrated without a measurement.
A re-apply writes only when the output would change, goes through the same
write as a tick (`limits`, `max_rate`), and is skipped in `MANUAL`, while
held or frozen, and while the rig would hold the write. The recorder keeps
it as a tick with `reapplied: true` and no measured value.

### Resuming a ramp after a hold

A trajectory's clock keeps running while the controller is held or
frozen, so by the time it resumes the setpoint has moved on without it. On
resume the controller re-seeds what is left of the ramp from the current
reading, at the ramp's own rate -- never faster -- so a step that has
further to go lands later rather than the law chasing the whole jump. A
`reseeded` event on the controller gives the old and the new end time. A
dwell is unchanged; a profile re-seeds its current segment and shifts the
later ones by the same amount.

### Why the law returns a correction

The law produces an *offset from the setpoint*, and the output starts from
the feedforward's own mapping of the setpoint. Two things follow:

- **Open loop is a law that returns zero** (`open_loop`, `OpenLoop`),
  run under `REGULATING` like any other law; it is not a mode. The output
  is then driven at the feedforward of the setpoint alone — how an
  experiment or a manual hold is run through the same path as regulation.
  The faceplate says "open loop" when a controller runs it.
- **Handover is arithmetic.** Switching from manual to regulating, or
  swapping a tuning, means choosing what the correction should be at the
  instant of the switch — a `Transfer`: `none` (leave the law and its
  correction as they are), `cold` (start the law cold: correction zero),
  `carry` (keep the old correction),
  or `track` (match the output already being delivered, the default,
  bumpless). See [The loop in detail](../6-internals/controller.md#handover).

### Modes

| mode | what a tick does |
| --- | --- |
| `MANUAL` | nothing; the output is driven by demands directly |
| `REGULATING` | update the law, then write (under `open_loop` the law's correction is zero) |

A frozen controller (a stale measured signal, a limit not known, a
measured reading with no value) is still `REGULATING`: frozen is a
condition, not a mode.

Mode says what the controller is *doing*. Who is *allowed* to change it —
a program step, an operator, the API — is a separate question, answered
separately: `rig.write` refuses a manual write against a signal a
controller is driving, so mode also decides who may write. A device command
that changes what drives the device -- one with a `mode`, an argument
linked to a demand, or `writes=` (a relay's `on`/`off`, a PWM channel's
`off`) -- is refused while a regulating controller drives one of the
device's demands, unless the command interrupts (`stop`, a manual flow). An
interrupting command puts the controller in manual only once it has
succeeded, and its response names it (`interrupted: [{controller, was}]`);
one that fails leaves the controller regulating.

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

## `on_fault`: what a controller does about a faulty source

A controller whose measured signal has no value freezes at once
([One tick](#one-tick)): the law does not step and the output keeps its
last demand. `on_fault` says what happens after that, once the fault time
it has accrued reaches its wait. The cost of every choice: until the
action fires, the output stays at its last demand. Keep the measured
signal's `poll_s` well inside the plant's time to harm.

| `on_fault` | once the wait is reached | latched |
| --- | --- | --- |
| `freeze` (the default) | nothing: the law stays frozen, the output where it was, and the controller resumes by itself after 3 readings in a row with a value | no |
| `manual` | the controller goes to manual; the output keeps its last value | the controller: its `regulate` is refused |
| `stop` | the controller goes to manual, and its output's [resolved stop](devices/index.md#stop-what-a-stop-writes) is written (the whole device's stop, where a command stops the device) | the controller and its output signal (or its device): every write to them is refused, a person's included |
| `stop_device` | the controller goes to manual, and its output's whole device is stopped | the controller and the output's device: every write to it is refused |
| `{freeze_s: 30, then: stop}` | frozen until 30 s of fault time has accrued, then `then` (`manual`, `stop` or `stop_device`) | as `then` |

**The wait.** `manual`, `stop` and `stop_device` fire once per outage, when
the accrued fault time reaches `max(2 × poll_s, 1 s)` for a single bad
observation (`invalid`, a device offline or hung, a failed write), and at
once for staleness by age (the signal's `stale_after_s` was the grace).
`{freeze_s: d, then: a}` waits `d` seconds of accrued fault time instead: a
reading with a value in between pauses the count but never resets it, so a
flickering source is not held forever. They act only while the controller
is regulating; `pending` and `not_applicable` never fire. Plain `freeze`
never acts.

**A law that raises** fires at once and takes at least `manual`: `freeze`
becomes `manual`; the others act as written.

**Refused at load.** `on_fault: stop` on an output whose stop resolves to
`keep` -- no `stop:` value, no driver `off`, no stop command -- is refused:
it would do nothing and look as if it did. Give the output a
[`stop:`](devices/index.md#stop-what-a-stop-writes) value, or choose
another action.

**The latch.** Each action but `freeze` latches, with the cause
`on_fault:<controller>` (`on_fault:heaters.heater2`): a condition `latched`
on each thing it holds, an `on_fault` event on the controller
(`{action, reason, accrued_s, was, stop}`), and the latch kept in the store,
so a restart keeps it and writes the stop again. While any latch holds the
controller, its output or the rig, `regulate` is refused (409) -- a program's
`ramp` or `regulate` step and an agent cannot clear one. A latching fault
also interrupts a running program, with the reason `fault:<controller>`,
whatever the program names.

**Reset.** Each latch is cleared only by its own Reset, by a person holding
`operate`: `POST /api/rig/reset {"cause": "on_fault:heaters.heater2"}`
([Stopping the rig](../1-running/runner/access.md#reset)). A fault's Reset
also clears the controller's law state, so a NaN that made the law raise
does not persist. Nothing resumes: the controller stays in manual. The one
shortcut: a person's `regulate` through HTTP clears the controller's own
`on_fault: manual` latch, which holds nothing else.

**Not a guarantee.** The wait and `freeze_s` are timed inside the runner,
best-effort: if flyball is stuck or dead, nothing acts
([What flyball does not do](../0-overview/limits.md)). `GET /api/rig/stop`
warns about a controller on plain `freeze` whose output's stop is its
driver's `off`, since that output is held indefinitely while the source is
faulty.

Per-reason actions (a different action for `invalid` than for `stale`) and
presets are not built.

## A permissive on the output

A controller whose output has a
[`permissive:`](devices/index.md#permissive-a-write-only-while-another-signal-allows-it)
that does not allow the write is held (frozen, condition `not_permitted`)
rather than refused, and resumes when the permissive allows it again.

Next: [Programs](../1-running/programs/index.md).

