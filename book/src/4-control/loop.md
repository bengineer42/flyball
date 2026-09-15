# The loop in detail

`Loop[A: Actuator]` is one channel, one law, one actuator, one reference.
This chapter is the arithmetic; [The loop](../1-concepts/loop.md) is the
shape.

## State

| field | is |
| --- | --- |
| `reference` | a float, or a `SetPointGenerator` evaluated at each tick's instant |
| `correction` | what the law last produced |
| `demand` | `setpoint + correction`, what the actuator was last told |
| `expected` | what the actuator said it would deliver, or `None` |
| `delivered_correction` | `expected − setpoint`; fed back to the law as anti-windup |
| `mode` | `manual`, `open`, `regulating` |
| `reading` | the last reading on the channel |
| `offset_ns` | the law's clock origin; `elapsed` is measured from here |

`LoopSettings` (the law's config, `offset_ns`) changes only on a retune;
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
        self.demand = setpoint + self.correction
        self.expected = self.actuator.set_demand(self.demand)
        self.delivered_correction = None if self.expected is None else self.expected - setpoint
```

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
