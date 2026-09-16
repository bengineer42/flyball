# The controller

A **controller** is the unit of regulation: one source signal, one law, one
target signal, one reference. It is named by the address of the signal it
drives — since a writable signal has at most one controller, that address
is enough.

## One tick

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
   blocking device's writer thread) or the driver cannot say.
5. **Remember** the reading, the demand and what was delivered, so the next
   tick's anti-windup and any `attach_on_tick` observer can see them.

Nothing here knows what the quantity is. The controller works in the
source's unit for the setpoint and the target's for the demand throughout;
the **feedforward** is what maps one to the other (`Setpoint`, the
identity, when the units agree; an `Affine` or `Table` curve; `NoFeedforward`
when there is none — a target with no static relationship to its source).

## Why a correction, not an output

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
  bumpless). See [The loop in detail](../4-control/loop.md#handover).

## Modes

| mode | what a tick does |
| --- | --- |
| `MANUAL` | nothing; the target is driven by demands directly |
| `OPEN` | write the feedforward against the held correction, law not stepped — an experiment or a manual hold run through the same path as regulation |
| `REGULATING` | step the law, then write |

Mode says what the controller is *doing*. Who is *allowed* to change it —
a program step, an operator, the API — is a separate question, answered
separately: `rig.demand` refuses a manual write against a signal a
controller is driving, so mode also decides who may write.

## Time

A law never owns a clock. It is given `elapsed` — seconds since it was last
reset or resumed — and derives its own interval from the previous call. A
generator, by contrast, is a function of *absolute* time, because a ramp is
anchored to the instant it must land. That split is deliberate and is why
the two take different time arguments.

## `min_period_s`

A source can update faster than a controller should step its law — an
oversampled sensor, a fast bus. `min_period_s` lets every reading update
`controller.reading` (so a client watching it always sees the latest
value) while the law only steps, and the demand only changes, at that
minimum interval.

Next: [Programs](programs.md).
