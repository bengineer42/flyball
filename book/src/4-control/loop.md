# The loop in detail

`Loop[A: Actuator]` is one channel, one law, one actuator, one reference,
and one feedforward. This chapter is the arithmetic;
[The loop](../1-concepts/loop.md) is the shape.

## State

| field | is |
| --- | --- |
| `reference` | a float, or a `SetPointGenerator` evaluated at each tick's instant |
| `correction` | what the law last produced, in the actuator's unit |
| `demand` | `feedforward(setpoint) + correction`, what the actuator was last told |
| `expected` | what the actuator said it would deliver, or `None` |
| `delivered_correction` | `expected − feedforward(setpoint)`; fed back to the law as anti-windup |
| `mode` | `manual`, `open`, `regulating` |
| `reading` | the last reading on the channel |
| `offset_ns` | the law's clock origin; `elapsed` is measured from here |

`LoopSettings` (the law's config, the feedforward's, the actuator's
`demand_unit`, `offset_ns`) changes only on a retune;
`LoopState` (everything above) changes every tick. `LoopView` joins them. The
telemetry cells carry the state per tick and join the settings on at flush,
so a retune shows without the tick paying for it.

## The tick

```python
def tick(self, reading):
    time_ns = reading.time_ns
    self.reading = reading
    if self.mode.active():                              # open or regulating
        setpoint = self.setpoint_at(time_ns)
        if self.mode is REGULATING:
            self.correction = self.law.step(
                self.to_law_time(time_ns), reading.value, setpoint, self.delivered_correction
            )
        base = self.feedforward(setpoint, self.rate_at(time_ns))
        self.demand = base + self.correction
        self.expected = self.actuator.set_demand(self.demand)
        self.delivered_correction = None if self.expected is None else self.expected - base
```

## The feedforward

The feedforward is the open-loop guess: the demand, in the *actuator's*
unit, that ought to hold the setpoint, which is in the *channel's*. The law
corrects the rest, so its gains are in actuator units per channel unit
(watts per °C on a bare heater; °C per °C on a packaged controller that
takes a temperature). Feedforwards are tagged and self-describing like laws
(`Feedforward` in `flyball.control.feedforward`; subclassing generates the
config and registers the tag), and a rig file names one per loop:

| tag | `demand =` | for |
| --- | --- | --- |
| `setpoint` | `setpoint` | an actuator that takes the channel's unit; the default when the units agree |
| `none` | `0` | a bare actuator under PID; the default when they differ |
| `affine` | `gain · setpoint + bias [+ rate_gain · rate]` | a plant that is linear near one point |
| `table` | interpolated `(setpoint, demand)` points, flat past the ends`[+ rate_gain · rate]` | a static curve measured at commissioning |

### The setpoint's rate

`rate` is `dSP/dt`, in the channel's unit per *second*: the loop asks the
reference for it (`Loop.rate_at`, `SetPointGenerator.rate`), rather than
differencing successive setpoints, which would carry the reading noise a
real trajectory does not have. `LinearRampSetpoint` reports its
`per_second` while ramping and `0` once it has landed; every other
reference is `0` always, since it is not moving.

`affine` and `table` take an optional `rate_gain`, actuator unit per
channel-unit-per-second, adding `rate_gain * rate` on top of the static
curve. It models a plant with *capacity*: a `table` of a furnace zone's
static losses gets the steady-state hold power right but, on a ramp, only
adds to a correction the law's integral is already winding up to cover the
same shortfall (see `examples/simulated/furnace.toml`'s comment: this
measured *worse* than plain PI). `rate_gain` covers the extra power a ramp
spends charging the zone's thermal mass -- `capacity_j_per_k` itself,
because it is J/K, i.e. W per °C/s, the same unit `rate_gain` wants. On
furnace zone 2 (6000 W, 3000 J/K), a 15 °C/min ramp to 700 °C then a hold
measured 4.0 °C overshoot plain PI, 7.6 °C with the static table alone, and
2.2 °C with `rate_gain = 3000` -- clearly better than plain PI, so that
loop ships with it. Not on `setpoint`: it already hands the actuator the
channel's own unit, so a rate term there would be a lead compensator, a
different job from the plant-capacity model this is; nothing needs it yet.

`resolve_value` and the bumpless seed use the same `feedforward(setpoint,
rate)` and its inverse (below), so a handover mid-ramp accounts for the
rate term rather than momentarily forgetting it.

### Inverting it

`Feedforward.invert(demand, rate)` is the setpoint behind a demand: `regulate(at=DEMAND)`
needs it, since `demand` is in the actuator's unit but the aim it sets must
be in the channel's like every other source. `setpoint` inverts to the
identity; `affine` solves the line (`ValueError`-family
`FeedforwardNotInvertibleError` if `gain` is 0); `table` reads its points
backwards where they are monotonic (rising or falling), the same error
otherwise. `none` has no inverse at all: every setpoint gives the same
demand (0), so a demand does not identify one.

`attach_loop` refuses `setpoint` when the units differ: handing a heater
"300" meaning °C when it reads watts would run happily and do nonsense.
The bumpless seed on `regulate` is `held − feedforward(setpoint, rate)`, so a
handover from manual holds the output whatever the units, mid-ramp included.

`to_law_time` is `(time_ns − offset_ns) / 1e9`: seconds since the law's own
start. The law keeps no clock; it derives its interval from the previous
call, so the first step and a repeated instant have zero interval and add
nothing to an integral.

## The reference

A `SetPointGenerator` is a function of *absolute* time, evaluated exactly at
the reading's timestamp rather than sampled once per tick. So a ramp lands
where it should whatever the tick rate, and a reference can be evaluated
*ahead* — at `t + dead_time` — for feedforward.

`LinearRampSetpoint(pace, end)` walks from wherever the loop is when it
starts to `end`, at a pace given as a duration or a rate. Its `signal` fires
when it lands, so a program step can wait on it.

## Handover

`regulate(at, generator, tuning, transfer)` is the one way into `regulating`
mode, and the one place a tuning changes. It does four things in a fixed
order:

1. Resolve `at` — a number, or `PROCESS` (the last reading), `SETPOINT` or
   `DEMAND` (the current ones) — and set the reference.
2. Swap in `tuning`, if given.
3. Seed the correction according to `transfer`.
4. Apply once.

The aim is set *before* the law is seeded, so the seed reproduces the
delivered output against the new setpoint. Seeding first would size the
correction for the old setpoint and step the actuator by the difference.

| transfer | correction becomes | meaning |
| --- | --- | --- |
| `NONE` | untouched | do not hand over |
| `RESET` | zero, law reset | cold start |
| `CARRY` | what the law already held | keep the offset |
| `TRACK` | what holds the delivered output | bumpless |

`TRACK` asks the law to `resume(reading, setpoint, held − setpoint)`: *set
your state so your next output is this; return what you managed.* A PI
seeds its integral and hits it exactly. A P law has no memory and returns
what it can. The difference between asked and returned is the **bump** —
the step the handover put through the actuator — and `regulate` returns it.

Where a mode cannot be honoured — nothing delivered to track, no reading to
compute a proportional term from — it degrades to a cold start, and the bump
is how the caller learns that it did.

## Anti-windup

Back-calculation. Each tick the law is told what the actuator actually
delivered, as a correction, and pulls its integral back by the difference
from what it asked for, at a rate set by the tracking time `tt`. An actuator
that returns `None` from `set_demand` gets no anti-windup rather than a wrong
one.

## Mode versus permission

`mode` says what the loop is doing. Whether a caller is *allowed* to change
it is a separate question the loop does not answer; `flyball.core.resource`
holds a claim graph for that, and whether it survives is an open question.
