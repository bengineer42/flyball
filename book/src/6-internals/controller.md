# The controller in detail

`Controller` binds one publishing **source** signal, through one law and a
feedforward, to one writable **target** signal — one reference, one law, one
target. It is named after the target it drives (`controller.name` is
`target.address`). This chapter is the arithmetic;
[How a controller works](../2-config/controllers.md#how-a-controller-works) is the shape. Programs still name a
controller with a `loop:` field (the target's address) — the class is
`Controller`, the concept is "the loop a controller closes", and the field
name outlived the rename.

## State

`ControllerSettings` — changes only on a retune or a `regulate`:

| field | is |
| --- | --- |
| `name` / `target` | the target signal's address (the two are the same) |
| `source` | the source signal's address |
| `law` | the law's config, or `None` before one is set |
| `feedforward` | what maps the source's unit to the target's |
| `demand_unit` | the target's unit symbol |
| `offset_ns` | the law's clock origin; `elapsed` is measured from here |
| `min_period_s` | step the law at most this often, however fast readings arrive; `None` steps on every reading |

`ControllerState` — changes every tick:

| field | is |
| --- | --- |
| `reference` | a float, or a `SetPointGenerator` evaluated at each tick's instant |
| `setpoint` | the reference resolved at the last tick, in the source's unit |
| `correction` | what the law last produced, in the target's unit |
| `demand` | `feedforward(setpoint, rate) + correction`, what the target was last told |
| `expected` | what the target committed to, or `None` while a commit is still pending |
| `delivered_correction` | `expected − feedforward(setpoint, rate)`; fed back to the law as anti-windup |
| `mode` | `manual`, `open`, `regulating` |
| `reading` | the last reading on the source |

`ControllerView` joins them (`ControllerView.of(settings, state)`); the wire
and `GET /api/controllers/{address}` carry it as `ControllerOut`.

## The tick

A source signal's node delivers a sample; the rig calls `controller.on_reading(reading)`
for every controller that source has, which asserts the reading is on
`self.source` and calls `tick(reading)`. A controller with no fresh reading —
the rig re-applying after a manual demand elsewhere, `apply()` — calls
`tick(None)`.

```python
# flyball/control/controller.py, trimmed
def tick(self, reading):
    time_ns = self.get_time_ns(reading and reading.time_ns)
    if reading is not None:
        self.reading = reading
    self._run_on_tick(reading)                            # attach_on_tick callbacks

    if (reading is not None and self.min_period_s is not None
            and self._last_step_ns is not None
            and time_ns - self._last_step_ns < self.min_period_s * 1e9):
        return                                # too soon: reading recorded, nothing else runs this tick

    if self.mode.active():                                  # open or regulating
        setpoint = self.setpoint_at(time_ns)
        if reading is not None and self.mode is ControllerMode.REGULATING:
            self._skip_outage(time_ns)                    # a gap counts as one ordinary step
            self._last_step_ns = time_ns
            self.correction = self.required_law.step(
                self.to_law_time(time_ns), reading.value, setpoint, self.delivered_correction
            )
        self._apply_demand(setpoint, self.rate_at(time_ns))


def _apply_demand(self, setpoint, rate=0.0):
    self.setpoint = setpoint
    self._base = base = self.feedforward(setpoint, rate)
    self.demand = base + self.correction
    self.expected = self.write(self.demand)
    self.delivered_correction = None if self.expected is None else self.expected - base
```

Six things to note:

1. **The reading is recorded, and `attach_on_tick` callbacks run, before the
   `min_period_s` gate.** A fast source updates `controller.reading` every
   time even when gated; the law steps, and the demand is re-applied, at
   most every `min_period_s` — so a slow integrator is not driven by noise
   from a source that polls faster than it needs to settle, and does not
   spend extra writes reapplying an unchanged demand between steps either.
2. **`manual` mode does nothing.** `mode.active()` is false only for
   `MANUAL`; both `OPEN` and `REGULATING` apply a demand every tick, the
   difference being whether the law steps first. `OPEN` freezes `correction`
   at whatever it last was and just keeps re-applying the feedforward against
   it; `open_loop` (the `ControlLaw` that always returns `0.0`) run under
   `REGULATING` is close but not identical — it forces `correction` to
   exactly zero every step rather than holding what was last there. `mode`
   is how a demand is forced through with the law suspended, without
   swapping laws.
3. **The demand is `feedforward(setpoint, rate) + correction`** — never
   `setpoint + correction` as such. `Setpoint`, the default feedforward
   when source and target units agree, makes the two the same thing;
   `Affine`/`Table`/`none` do not.
4. **An outage does not integrate.** A source that goes quiet (a sensor
   offline, a stalled poll) comes back with one reading after the whole
   gap. If the gap is more than `OUTAGE_STEPS` (3) of the usual step
   intervals, `_skip_outage` moves `offset_ns` on by the gap less one
   interval, so the law's `dt` for that step is one ordinary interval, not
   the outage; `reset_law` forgets the last step, so the first step after a
   `regulate` is never mistaken for one. This is an interim bound: what a
   controller does across and after an outage (freeze, reset, wait for fresh
   readings) is still to be decided. Separately, `regulate` and
   `set_reference` refuse a NaN or infinite setpoint before anything
   changes, and the rig runs each controller's step on its own
   (`Rig._step`): one that raises is a `step_failed` event and does not stop
   the others.
5. **`self.write(self.demand)` is how the demand reaches the target.** Built
   with no rig, `write` is `Controller._unwired`, which always returns
   `None`: the demand is recorded on the controller (`controller.demand`)
   and nothing is written — the shape a unit test on a law wants. Attached to
   a rig (`Rig.attach_controller`), `write` is a closure the rig builds:

   ```python
   def write(demand: float) -> float | None:
       states = self.demand(target.node, {target: demand}, by=controller)
       return None if (state := states.get(target)) is None else state.value
   ```

   `rig.demand(...)` validates and clamps the value, then either commits it
   at once (a manual demand, or a controller ticking outside a delivery) or,
   when the controller is ticking *inside* one (`Rig.on_samples`, mid-delivery,
   several controllers and a bound input sharing one device's commit),
   returns `{}` — nothing committed yet — and `write` passes `None` back.
   `self.expected` is then `None` and `delivered_correction` is `None` too,
   until the delivery's single `device.commit()` runs at its end and
   [`delivered(state)`][flyball.model.controller.Controller.delivered]
   fills them in, closing the tick with what the target actually took.
6. **A target that never reports (`write` always returns `None`) gets no
   anti-windup.** `step`'s `last_applied` argument is `self.delivered_correction`
   from the *previous* tick; `None` skips the back-calculation term (see
   below), so an unwired or permanently-deferred controller runs open,
   correction-wise, exactly as a law with no `tt` would.

## The feedforward

The feedforward is the open-loop guess: the demand, in the **target's**
unit, that ought to hold the setpoint, which is in the **source's**. The law
corrects the rest, so its gains are in target units per source unit (watts
per °C on a bare heater; °C per °C on a packaged controller that itself
takes a temperature). Feedforwards are tagged and self-describing like laws
(`Feedforward` in `flyball.model.feedforward`; subclassing generates the
config, and `control/configs.py` registers the built-in tags on a
`Catalogs`), and a rig file names one per controller:

| tag | `demand =` | for |
| --- | --- | --- |
| `setpoint` | `setpoint` | a target that takes the source's unit; the default when the units agree |
| `none` | `0` | a bare target under PID; the default when they differ |
| `affine` | `gain · setpoint + bias [+ rate_gain · rate]` | a plant that is linear near one point |
| `table` | interpolated `(setpoint, demand)` points, flat past the ends`[+ rate_gain · rate]` | a static curve measured at commissioning |

`Controller.__init__` refuses `feedforward=Setpoint()` when the units
differ (`ConflictError`): handing a heater "300" meaning °C when it takes
watts would run happily and do nonsense. Without a units mismatch to force
`none`, and with none given, `Setpoint()` is built automatically.

### The setpoint's rate

`rate` is `dSP/dt`, in the source's unit per *second*: the controller asks
the reference for it (`Controller.rate_at`, `SetPointGenerator.rate`),
rather than differencing successive `setpoint_at` values, which would carry
the reading noise a real trajectory does not have. `LinearRampSetpoint`
reports its `per_second` while ramping and `0` once it has landed; every
other reference is `0` always, since it is not moving.

`affine` and `table` take an optional `rate_gain`, target unit per
source-unit-per-second, adding `rate_gain * rate` on top of the static
curve. It models a plant with *capacity*: a `table` of a furnace zone's
static losses gets the steady-state hold power right but, on a ramp, only
adds to a correction the law's integral is already winding up to cover the
same shortfall — see `examples/furnace/rig.yaml`'s comment: this
measured *worse* than plain PI. `rate_gain` covers the extra power a ramp
spends charging the zone's thermal mass — `capacity_j_per_k` itself,
because it is J/K, i.e. W per °C/s, the same unit `rate_gain` wants. On
furnace zone 2 (6000 W, 3000 J/K), a 15 °C/min ramp to 700 °C then a hold
measured 4.0 °C overshoot plain PI, 7.6 °C with the static table alone, and
2.2 °C with `rate_gain: 3000` — clearly better than plain PI, so that
controller ships with it:

```yaml
# examples/furnace/rig.yaml
heaters.heater2:
  signal: furnace.zone2
  law: { tag: PI, kp: 100, ki: 0.15, tt: 30 }
  feedforward:
    tag: table
    rate_gain: 3000
    points: [[20, 0], [100, 125.4], [200, 289.4], ...]
  default: true
```

Not on `setpoint`: it already hands the target the source's own unit, so a
rate term there would be a lead compensator, a different job from the
plant-capacity model this is; nothing needs it yet.

`resolve_value` and the bumpless seed use the same `feedforward(setpoint,
rate)` and its inverse (below), so a handover mid-ramp accounts for the
rate term rather than momentarily forgetting it.

### Inverting it

`Feedforward.invert(demand, rate)` is the setpoint behind a demand:
`regulate(at=ValueSource.DEMAND)` needs it, since `demand` is in the
target's unit but the aim it sets must be in the source's like every other
source. `Controller.resolve_value` does the conversion, and only there:
`DEMAND` is the one `ValueSource` that runs through `invert` rather than
being read straight off the controller's state. `setpoint` inverts to the
identity; `affine` solves the line (`FeedforwardNotInvertibleError` if
`gain` is 0); `table` reads its points backwards where they are monotonic
(rising or falling), the same error otherwise. `none` has no inverse at
all: every setpoint gives the same demand (0), so a demand does not
identify one.

`to_law_time` is `(time_ns − offset_ns) / 1e9`: seconds since the law's own
start. The law keeps no clock; it derives its interval from the previous
call, so the first step and a repeated instant have zero interval and add
nothing to an integral.

## The reference

A `SetPointGenerator` is a function of *absolute* time, evaluated exactly at
the reading's timestamp (`setpoint_at(time_ns)`, inside `tick`) rather than
sampled once per tick. So a ramp lands where it should whatever the tick
rate, and a reference can be evaluated *ahead* — at `t + dead_time` — for
feedforward.

`LinearRampSetpoint(pace, end)` walks from wherever the controller is when
it starts to `end`, at a pace given as a `Duration` or a `Speed`. Its
`signal` (a `Trigger`) fires when it lands, so a program step can wait on
it.

## Handover

`regulate(at, generator, tuning, transfer, time_ns)` is the one way into
`REGULATING` mode, and the one place a tuning changes. It does four things
in a fixed order:

1. Resolve `at` — a number, or `ValueSource.PROCESS` (the last reading),
   `SETPOINT` or `DEMAND` (the current ones) — and set `reference` (and, if
   `generator` is given, start it there and use it as the reference
   instead).
2. Swap in `tuning`, if given.
3. Seed the correction according to `transfer`.
4. Apply once — the same `_apply_demand` a tick runs.

The aim is set *before* the law is seeded, so the seed reproduces the
delivered output against the new setpoint. Seeding first would size the
correction for the old setpoint and step the target by the difference.

| transfer | correction becomes | meaning |
| --- | --- | --- |
| `NONE` | untouched, law not reset | do not hand over |
| `RESET` | zero, law reset | cold start |
| `CARRY` | what the law already held | keep the offset |
| `TRACK` | what holds the delivered output | bumpless |

`held` — what the target was last actually committed to (`self.expected`),
or, if nothing has been committed yet, last demanded (`self.demand`) — is
captured at the very top of `regulate()`, before the aim moves. For anything
but `NONE`, the law is reset next (`reset_law`, which also moves `offset_ns`
to the handover instant). Then, unless there is no reading yet or `transfer
is RESET` (both cold-start straight to `correction = 0.0`, with no call to
the law), the correction is seeded by asking the law `resume(reading,
setpoint, hold)`: *set your state so your next output is `hold`; return
what you managed.* `hold` is `held − feedforward(setpoint, rate)` for
`TRACK` when something has already been held; `CARRY`, and `TRACK` with
nothing held yet, instead pass `hold = self.correction` — the offset the law
is already carrying, run back through `resume` rather than left untouched,
so the seed is still whatever the law itself can reproduce of it. A PI seeds
its integral and hits `hold` exactly. A P law has no memory and returns what
it can regardless of `hold`. The difference between asked and returned is
the **bump** — the step the handover puts through the target —
`regulate()` returns it in its `RegulateResult`.

Where a mode cannot be honoured — nothing delivered to track, no reading to
compute a proportional term from — it degrades to a cold start (`correction
= 0.0`), and the bump is how the caller learns that it did.

`manual()` only sets `mode = MANUAL`; it does not touch `reference`,
`correction` or the target, so the target keeps its last demand until
something else writes to it. `set_reference(at, generator, time_ns)` moves
`reference` (and, given a generator, restarts it) without touching `mode`
at all — the `PUT /api/controllers/{address}/reference` route.

## Anti-windup

Back-calculation. `PI`/`PID`'s `step_integral` is handed `last_applied` —
`self.delivered_correction` from the *previous* tick, i.e. what the target
actually reported committing minus what the feedforward alone asked for —
and pulls the integral back by `(last_applied − last_raw) * dt / (tt * ki)`,
where `last_raw` is the raw (unclamped) correction the law itself computed
last step. A target whose `write` returns `None` — unwired, or a commit
still pending inside a delivery — reports no `delivered_correction`, so that
tick gets no anti-windup term rather than a wrong one. `tt` omitted or 0
turns this off outright (`(tt * ki)` is 0, so the term is skipped rather
than dividing by zero): a reasonable `tt` is about `Ti` (`kp/ki`), or
`√(Ti·Td)` once a derivative term also acts.

`smith`'s own internal model is driven by the same `last_applied` when it is
given, in place of the law's own last output — a clamp or a deferred commit
downstream must not leave the model believing more correction reached the
plant than really did.

## Mode versus permission

`mode` says what the controller is doing. Whether a caller is *allowed* to
change it is a separate question the controller does not answer;
`flyball.foundation.resource` holds a claim graph for that.
