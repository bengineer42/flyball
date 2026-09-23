# The controller in detail

`Controller` regulates one publishing **measured** signal, through one law
and a feedforward, by writing one demand, its **output** — one reference,
one law, one output. It is named after the output it drives
(`controller.name` is `output_signal.address`). This chapter is the arithmetic;
[How a controller works](../2-config/controllers.md#how-a-controller-works) is the shape. Programs still name a
controller with a `loop:` field (the output's address) — the class is
`Controller`, the concept is "the loop a controller closes", and the field
name outlived the rename.

## State

`ControllerSettings` — changes only on a retune or a `regulate`:

| field | is |
| --- | --- |
| `name` / `output_signal` | the output's address (the two are the same) |
| `measured_signal` | the measured signal's address |
| `law` | the law's config, or `None` before one is set |
| `feedforward` | what maps the measured unit to the output's |
| `output_unit` | the output's unit symbol |
| `offset_ns` | the law's clock origin; `elapsed` is measured from here |
| `min_period_s` | step the law at most this often, however fast readings arrive; `None` steps on every reading |

`ControllerState` — changes every tick:

| field | is |
| --- | --- |
| `reference` | a float, or a `SetPointGenerator` evaluated at each tick's instant |
| `setpoint` | the reference resolved at the last tick, in the measured unit |
| `correction` | what the law last produced, in the output's unit |
| `output` | `feedforward(setpoint, rate) + correction`, what the output signal was last told |
| `expected` | what the output signal committed to, or `None` while a commit is still pending |
| `delivered_correction` | `expected − feedforward(setpoint, rate)`; fed back to the law as anti-windup |
| `mode` | `manual`, `open`, `regulating` |
| `measured` | the last reading on the measured signal |

`ControllerView` joins them (`ControllerView.of(settings, state)`); the wire
and `GET /api/controllers/{address}` carry it as `ControllerOut`.

## The tick

A measured signal's node delivers a sample; the rig calls `controller.on_reading(reading)`
for the controller regulating it, which asserts the reading is on
`self.measured_signal` and calls `tick(reading)`. A controller with no fresh reading —
the rig re-applying after a manual demand elsewhere, `apply()` — calls
`tick(None)`.

```python
# flyball/control/controller.py, trimmed
def tick(self, reading):
    time_ns = self.get_time_ns(reading and reading.time_ns)
    if reading is not None:
        self.measured = reading
    self._run_on_tick(reading)                            # attach_on_tick callbacks

    if (reading is not None and self.min_period_s is not None
            and self._last_step_ns is not None
            and time_ns - self._last_step_ns < self.min_period_s * 1e9):
        return                                # too soon: reading recorded, nothing else runs this tick

    if self.mode.active():                                  # open or regulating
        if (reason := self.hold()) is not None:           # the rig would refuse the write
            self.held = reason                            # frozen: no step, no write
            return
        resumed, self.held = self.held is not None, None
        setpoint = self.setpoint_at(time_ns)
        if reading is not None and self.mode is ControllerMode.REGULATING:
            self._skip_outage(time_ns, resumed=resumed)   # a gap counts as one ordinary step
            self._last_step_ns = time_ns
            self.correction = self.required_law.step(
                self.to_law_time(time_ns), reading.value, setpoint, self.delivered_correction
            )
        self._apply_output(setpoint, self.rate_at(time_ns))


def _apply_output(self, setpoint, rate=0.0):
    self.setpoint = setpoint
    self._base = base = self.feedforward(setpoint, rate)
    self.output = base + self.correction
    self.expected = self.write(self.output)
    self.delivered_correction = None if self.expected is None else self.expected - base
```

Seven things to note:

1. **The reading is recorded, and `attach_on_tick` callbacks run, before the
   `min_period_s` gate.** A fast measured signal updates `controller.measured` every
   time even when gated; the law steps, and the output is re-applied, at
   most every `min_period_s` — so a slow integrator is not driven by noise
   from a signal that polls faster than it needs to settle, and does not
   spend extra writes reapplying an unchanged output between steps either.
2. **`manual` mode does nothing.** `mode.active()` is false only for
   `MANUAL`; both `OPEN` and `REGULATING` apply an output every tick, the
   difference being whether the law steps first. `OPEN` freezes `correction`
   at whatever it last was and just keeps re-applying the feedforward against
   it; `open_loop` (the `ControlLaw` that always returns `0.0`) run under
   `REGULATING` is close but not identical — it forces `correction` to
   exactly zero every step rather than holding what was last there. `mode`
   is how a demand is forced through with the law suspended, without
   swapping laws.
3. **The output is `feedforward(setpoint, rate) + correction`** — never
   `setpoint + correction` as such. `Setpoint`, the default feedforward
   when the measured and output units agree, makes the two the same thing;
   `Affine`/`Table`/`none` do not.
4. **An outage does not integrate.** A measured signal that goes quiet (a sensor
   offline, a stalled poll) comes back with one reading after the whole
   gap. If the gap is more than `OUTAGE_STEPS` (3) of the usual step
   intervals, `_skip_outage` moves `offset_ns` on by the gap less one
   interval, so the law's `dt` for that step is one ordinary interval, not
   the outage; `reset_law` forgets the last step, so the first step after a
   `regulate` is never mistaken for one. This is an interim bound for a
   signal that goes quiet; a measured signal the rig calls stale is a hold (7).
   Separately, `regulate` and
   `set_setpoint` refuse a NaN or infinite setpoint before anything
   changes, and the rig runs each controller's step on its own
   (`Rig._step`): one that raises is a `step_failed` event and does not stop
   the others.
5. **`self.write(self.output)` is how the output value reaches the output signal.** Built
   with no rig, `write` is `Controller._unwired`, which always returns
   `None`: the value is recorded on the controller (`controller.output`)
   and nothing is written — the shape a unit test on a law wants. Attached to
   a rig (`Rig.attach_controller`), `write` is a closure the rig builds:

   ```python
   def write(value: float) -> float | None:
       states = self.demand(output.node, {output: value}, by=controller)
       return None if (state := states.get(output)) is None else state.value
   ```

   `rig.demand(...)` validates and clamps the value, then either commits it
   at once (a manual demand, or a controller ticking outside a delivery) or,
   when the controller is ticking *inside* one (`Rig.on_samples`, mid-delivery,
   several controllers and a bound input sharing one device's commit),
   returns `{}` — nothing committed yet — and `write` passes `None` back.
   `self.expected` is then `None` and `delivered_correction` is `None` too,
   until the delivery's single `device.commit()` runs at its end and
   [`delivered(state)`][flyball.model.controller.Controller.delivered]
   fills them in, closing the tick with what the output actually took.
6. **An output that never reports (`write` always returns `None`) gets no
   anti-windup.** `step`'s `last_applied` argument is `self.delivered_correction`
   from the *previous* tick; `None` skips the back-calculation term (see
   below), so an unwired or permanently-deferred controller runs open,
   correction-wise, exactly as a law with no `tt` would.
7. **A held write freezes the controller.** Before stepping, the controller
   asks `self.hold()` -- the rig's
   [`hold_reason`][flyball.rig.rig.Rig.hold_reason], injected like `write`
   -- whether the rig would refuse its write: `stale_input` (the measured signal is
   older than `stale_after`) or `limit_unknown` (a limit on the output
   follows a signal with no finite value, D-030). If so the tick returns
   there: the law does not step, `correction`, `output`, `expected` and
   `delivered_correction` keep their last values, and `held` records the
   reason. Stepping anyway would integrate the error against a write that
   never lands -- with no `delivered_correction`, back-calculation is off --
   and slam the output when the hold ends; `smith` would also drive its
   model with an output the plant never saw. The first step after the hold
   is `_skip_outage(..., resumed=True)`: `offset_ns` moves on by the gap
   less one usual interval (the whole gap if there is no usual interval
   yet), so the law's `dt` is at most one ordinary interval and the result
   is what it would have been had the held ticks never happened. The rig
   emits each hold's event once, on entering it (`limit_known` on leaving a
   `limit_unknown` hold), and `demand(by=controller)` asks `hold_reason`
   again for the write itself. Unattached, `hold` is
   `Controller._never_held`.

## The feedforward

The feedforward is the open-loop guess: the output value, in the
**output's** unit, that ought to hold the setpoint, which is in the
**measured** signal's. The law corrects the rest, so its gains are in
output units per measured unit (watts
per °C on a bare heater; °C per °C on a packaged controller that itself
takes a temperature). Feedforwards are tagged and self-describing like laws
(`Feedforward` in `flyball.model.feedforward`; subclassing generates the
config, and `control/configs.py` registers the built-in tags on a
`Catalogs`), and a rig file names one per controller:

| tag | `output =` | for |
| --- | --- | --- |
| `setpoint` | `setpoint` | an output that takes the measured unit; the default when the units agree |
| `none` | `0` | a bare output under PID; the default when they differ |
| `affine` | `gain · setpoint + bias [+ rate_gain · rate]` | a plant that is linear near one point |
| `table` | interpolated `(setpoint, output)` points, flat past the ends`[+ rate_gain · rate]` | a static curve measured at commissioning |

`Controller.__init__` refuses `feedforward=Setpoint()` when the units
differ (`ConflictError`): handing a heater "300" meaning °C when it takes
watts would run happily and do nonsense. Without a units mismatch to force
`none`, and with none given, `Setpoint()` is built automatically.

### The setpoint's rate

`rate` is `dSP/dt`, in the measured unit per *second*: the controller asks
the reference for it (`Controller.rate_at`, `SetPointGenerator.rate`),
rather than differencing successive `setpoint_at` values, which would carry
the reading noise a real trajectory does not have. `LinearRampSetpoint`
reports its `per_second` while ramping and `0` once it has landed; every
other reference is `0` always, since it is not moving.

`affine` and `table` take an optional `rate_gain`, output unit per
measured-unit-per-second, adding `rate_gain * rate` on top of the static
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
  measured: furnace.zone2
  law: { tag: PI, kp: 100, ki: 0.15, tt: 30 }
  feedforward:
    tag: table
    rate_gain: 3000
    points: [[20, 0], [100, 125.4], [200, 289.4], ...]
  default: true
```

Not on `setpoint`: it already hands the output the measured signal's own unit, so a
rate term there would be a lead compensator, a different job from the
plant-capacity model this is; nothing needs it yet.

`resolve_value` and the bumpless seed use the same `feedforward(setpoint,
rate)` and its inverse (below), so a handover mid-ramp accounts for the
rate term rather than momentarily forgetting it.

### Inverting it

`Feedforward.invert(output, rate)` is the setpoint behind an output value:
`regulate(at=ValueSource.OUTPUT)` needs it, since `output` is in the
output's unit but the aim it sets must be in the measured unit like every
other value. `Controller.resolve_value` does the conversion, and only there:
`OUTPUT` is the one `ValueSource` that runs through `invert` rather than
being read straight off the controller's state. `setpoint` inverts to the
identity; `affine` solves the line (`FeedforwardNotInvertibleError` if
`gain` is 0); `table` reads its points backwards where they are monotonic
(rising or falling), the same error otherwise. `none` has no inverse at
all: every setpoint gives the same output (0), so an output does not
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

1. Resolve `at` — a number, or `ValueSource.MEASURED` (the last reading),
   `SETPOINT` or `OUTPUT` (the current ones) — and set `reference` (and, if
   `generator` is given, start it there and use it as the reference
   instead).
2. Swap in `tuning`, if given.
3. Seed the correction according to `transfer`.
4. Apply once — the same `_apply_output` a tick runs.

The aim is set *before* the law is seeded, so the seed reproduces the
delivered output against the new setpoint. Seeding first would size the
correction for the old setpoint and step the output by the difference.

| transfer | correction becomes | meaning |
| --- | --- | --- |
| `NONE` | untouched, law not reset | do not hand over |
| `RESET` | zero, law reset | cold start |
| `CARRY` | what the law already held | keep the offset |
| `TRACK` | what holds the delivered output | bumpless |

`held` — what the output was last actually committed to (`self.expected`),
or, if nothing has been committed yet, last asked (`self.output`) — is
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
the **bump** — the step the handover puts through the output —
`regulate()` returns it in its `RegulateResult`.

Where a mode cannot be honoured — nothing delivered to track, no reading to
compute a proportional term from — it degrades to a cold start (`correction
= 0.0`), and the bump is how the caller learns that it did.

`manual()` only sets `mode = MANUAL`; it does not touch `reference`,
`correction` or the output, so the output keeps its last value until
something else writes to it. `set_setpoint(at, generator, time_ns)` moves
`reference` (and, given a generator, restarts it) without touching `mode`
at all — the `PUT /api/controllers/{address}/setpoint` route.

## Anti-windup

Back-calculation. `PI`/`PID`'s `step_integral` is handed `last_applied` —
`self.delivered_correction` from the *previous* tick, i.e. what the output
actually reported committing minus what the feedforward alone asked for —
and moves the integral's contribution toward it by
`(last_applied − last_raw) * (1 − exp(−dt / tt))`, where `last_raw` is the
raw (unclamped) correction the law itself computed last step. That is the
continuous law `dI/dt = (last_applied − last_raw) / tt` integrated exactly
over the step, so the gap closes by at most all of it: the output never
crosses what was applied, however long `dt` is. (The forward-Euler form it
replaced, `* dt / tt`, overshot once `dt > tt` and diverged past
`2·tt`: a long step off a railed output swung it far past the rail.) An output whose `write` returns `None` — unwired, or a commit
still pending inside a delivery — reports no `delivered_correction`, so that
tick gets no anti-windup term rather than a wrong one. `tt` omitted or 0
(or `ki` 0) turns this off outright: a reasonable `tt` is about `Ti` (`kp/ki`), or
`√(Ti·Td)` once a derivative term also acts.

`smith`'s own internal model is driven by the same `last_applied` when it is
given, in place of the law's own last output — a clamp or a deferred commit
downstream must not leave the model believing more correction reached the
plant than really did.

## Mode versus permission

`mode` says what the controller is doing. Whether a caller is *allowed* to
change it is a separate question the controller does not answer;
`flyball.foundation.resource` holds a claim graph for that.
