# The controller in detail

`Controller` regulates one published **measured** signal, through one law
and a feedforward, by writing one demand, its **output** — one reference,
one law, one output. It is named after the output it drives
(`controller.name` is `output_signal.address`). `Controller.__init__`
refuses an output whose role is not `Role.DEMAND` ("`'<address>' is a
setting, not a demand: a controller drives only demands`") and one without
`W` (a demand only its group drives, `RP`), both as `ConflictError` (409);
`GET /api/controllers/schema` offers only writable demands as `outputs`
(C13). A signal known only once its device is built (a `qcodes` or
`pymeasure` channel) is refused then, with the same message. This chapter is the arithmetic;
[How a controller works](../2-config/controllers.md#how-a-controller-works) is the shape. Programs name a
controller with a `controllers:` field (the output's address, or a list).

## State

`ControllerSpec` — changes only on a retune or a `regulate`:

| field | is |
| --- | --- |
| `name` / `output_signal` | the output's address (the two are the same) |
| `measured_signal` | the measured signal's address |
| `law` | the law's config, or `None` before one is set |
| `feedforward` | what maps the measured unit to the output's |
| `output_unit` | the output's unit symbol |
| `offset_ns` | the law's clock origin; `elapsed` is measured from here |
| `min_period_s` | update the law at most this often, however fast readings arrive; `None` updates on every reading |

`ControllerState` — changes every tick:

| field | is |
| --- | --- |
| `reference` | a float, or a `SetpointGenerator` evaluated at each tick's instant |
| `setpoint` | the reference resolved at the last tick, in the measured unit |
| `correction` | what the law last produced, in the output's unit |
| `output` | `feedforward(setpoint, rate) + correction`, what the output signal was last told |
| `expected` | what the output signal committed to, or `None` while a commit is still pending |
| `delivered_correction` | `expected − feedforward(setpoint, rate)`; fed back to the law as anti-windup |
| `mode` | `manual`, `regulating` |
| `measured` | the last reading on the measured signal |

`ControllerView` joins them (`ControllerView.of(settings, state)`); the wire
and `GET /api/controllers/{address}` carry it as `ControllerOut`.

## The tick

A measured signal's node delivers a sample; the rig calls `controller.on_reading(reading)`
for the controller regulating it, which asserts the reading is on
`self.measured_signal` and calls `tick(reading)`. `apply()` -- a caller
putting the output back after a manual demand elsewhere -- calls
`tick(None)`. Between readings the rig's timer calls
[`reapply`](#between-readings-reapply) instead, never `tick(None)`: a
`tick(None)` clears `held` and would use up a resume.

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

    if self.mode.active():                                  # regulating
        reason = self.hold()                              # the rig would refuse the write
        if reason is None and reading is not None and not reading.usable:
            reason = Code.FROZEN                          # no value: never stepped on, rig or not
        if reason is not None:
            self.held = reason                            # frozen: no step, no write
            return
        resumed, self.held = self.held is not None, None
        setpoint = self.setpoint_at(time_ns)
        if reading is not None:
            self._skip_outage(time_ns, resumed=resumed)   # a gap counts as one ordinary step
            self._last_step_ns = time_ns
            self.correction = self.required_law.update(
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
2. **`manual` mode does nothing.** `mode.active()` is true only for
   `REGULATING`, which steps the law and applies an output every tick. Open
   loop is a law, not a mode: `open_loop` (the `ControlLaw` that always
   returns `0.0`) runs under `REGULATING` and forces `correction` to exactly
   zero every step. (A mode that re-applied the feedforward against a held
   correction without stepping the law, `OPEN`, was never entered and was
   removed, D23.)
3. **The output is `feedforward(setpoint, rate) + correction`** — never
   `setpoint + correction` as such. `Setpoint`, the default feedforward
   when the measured and output units agree, makes the two the same thing;
   `Affine`/`Table`/`none` do not.
4. **An outage does not integrate.** A measured signal that goes quiet (a sensor
   offline, a stalled poll) comes back with one reading after the whole
   gap. If the gap is more than `OUTAGE_STEPS` (3) of the usual step
   intervals, `_skip_outage` moves `offset_ns` on by the gap less one
   interval, so the law's `dt` for that step is one ordinary interval, not
   the outage; `clear_law` forgets the last step, so the first step after a
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
       states = self.write(output.node, {output: value}, by=controller)
       return None if (state := states.get(output)) is None else state.value
   ```

   `rig.write(...)` validates and clamps the value, then either commits it
   at once (a manual demand, or a controller ticking outside a delivery) or,
   when the controller is ticking *inside* one (`Rig.on_samples`, mid-delivery,
   several controllers and a bound input sharing one device's commit),
   returns `{}` — nothing committed yet — and `write` passes `None` back.
   `self.expected` is then `None` and `delivered_correction` is `None` too,
   until the delivery's single `device.commit()` runs at its end and
   [`delivered(state)`][flyball.model.controller.Controller.delivered]
   fills them in, closing the tick with what the output actually took.
6. **An output that never reports (`write` always returns `None`) gets no
   anti-windup.** `update`'s `last_applied` argument is `self.delivered_correction`
   from the *previous* tick; `None` skips the back-calculation term (see
   below), so an unwired or permanently-deferred controller runs open,
   correction-wise, exactly as a law with no `tt_s` would.
7. **A held write freezes the controller.** Before stepping, the controller
   asks `self.hold()` -- the rig's
   [`hold_reason`][flyball.rig.rig.Rig.hold_reason], injected like `write`
   -- whether the rig would refuse its write: `stale_input` (the measured signal is
   older than `stale_after_s`), `limit_unknown` (a limit on the output
   follows a signal with no finite value, D-030), `not_permitted` (the
output's `permissive:` does not allow a write now) or `frozen` (the measured
   signal's newest reading is a `NoValue`; the controller holds `frozen`
   on such a reading by itself too, with no rig in front of it). If so the tick returns
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
   holds each reason as a condition on the controller in `rig.conditions`
   (`stale_input`, `limit_unknown`, `frozen`): set on every held call, which raises
   it once, and cleared on the first call that is not held, and `demand(by=controller)` asks `hold_reason`
   again for the write itself. `frozen` clears only once `RESUME_AFTER` (3)
   readings in a row have had a value: the rig counts them per controller as
   it steps it, and until then `hold_reason` keeps answering `frozen`.
   Unattached, `hold` is `Controller._never_held`. `last_value` is None
   while the newest reading has no value, so `regulate(ValueSource.MEASURED)`
   refuses, and a `TRACK`/`CARRY` handover seeds as if nothing had been read.
8. **Resuming re-seeds a trajectory.** A generator is a function of
   absolute time, so its clock ran on through the hold. On the first step
   after one, `_reseed(time_ns, reading)` asks the `SetpointGenerator` to
   `reseed(now_s, value)`: the segment in force walks on from the reading
   at its own rate, never faster, and so ends later if it has further to
   go. A dwell is left alone; a profile re-seeds its current segment and
   shifts the later ones. When the end moves, `on_reseed(was, end)` -- the
   rig's, injected like `hold` -- raises a `reseeded` event on the
   controller (`{end_was_s, end_s}`).
9. **`regulate` asks a guard first.** `self.guard()` -- the rig's
   `stopping.regulate_refusal`, injected at attach -- names a latch that
   holds the controller, its output or the rig; `regulate` then raises
   `ConflictError` (409) before anything changes. Nothing that regulates
   can clear a latch: only a person's Reset, or the HTTP route's own
   shortcut for a controller's `on_fault: manual` latch. Unattached, the
   guard is `Controller._never_refused`.

## Between readings: `reapply`

E25 (D-052). While a controller is `REGULATING` on a generator that has not
finished, through a feedforward other than `none` (`Controller.follows`),
the rig runs `reapply(time_ns)` every `setpoint_period_s` (default
`max(0.1 s, poll_s / 4)`) on its timers. It writes `feedforward(setpoint_at(now),
rate_at(now)) + correction` -- the last correction, unchanged -- and returns
whether it wrote. It returns at once in `MANUAL`, off a moving setpoint,
while `held` is set (it tests `held`, never assigns or clears it), when the
last measured reading has no value, when `hold()` gives a reason, and when
the output would not change. It never touches `_last_step_ns`,
`_step_interval_ns` or `offset_ns`, so the next reading steps the law as it
would have.

The controller tells the rig when its reference or mode changes
(`on_reference`, injected like `write` and `hold`, called by `regulate`,
`set_setpoint` and `manual`); the rig arms the periodic call then, and
cancels it off a moving setpoint without taking its lock (a stop puts every
controller in `MANUAL` without waiting behind a delivery). The call itself is
serialised like a delivery: under `rig.lock`, in its own `_touched`, one
commit, `controller_states` updated, and a tick recorded with `reapplied`
and no reading. A re-apply at the same instant as a reading's tick gives
way to it in the store.

## Fault time

The rig's `faults` ([`Faults`][flyball.rig.faults.Faults]) keeps, per
controller, the outage of its measured signal (A5): every delivery to the
controller (`Rig._step`) starts, pauses or ends it, and fault time accrues
on the rig clock only while the source is `invalid` or `stale`. On each
entry into fault-class a one-shot is armed for the wait still to go
(`max(2·poll_s, 1 s)` for a single observation, 0 for staleness by age or a
law that raises); a reading that is not a fault cancels it. When it comes
up it takes the rig's lock, checks again, and releases the outage -- once,
and only while `REGULATING` -- to `faults.on_fault`, which the rig sets to
[`Stopping.on_fault`][flyball.rig.stopping.Stopping.on_fault]. The
arithmetic ([`Accrual`][flyball.rig.faults.Accrual]) is shared with the
bands' `invalid` grace.

Freezing and acting are two separate things. The freeze is the tick's
own hold (7): immediate, per reading, and undone by itself after
`RESUME_AFTER` readings with a value. The release is the rig's, once per
outage, and acts on the controller's `OnFault`:

- `freeze` (the default) is never released: `on_fault` returns at once.
- `{freeze_s: d, then: a}` (`OnFault(action, freeze_s)`) arms the one-shot
  for `d` of accrued fault time instead of the reason's wait.
- A law that raises is released at once with the reason `law_error`, and
  takes `OnFault.escalated()`: the stricter of the configured action and
  `manual`.
- `manual`, `stop` and `stop_device` put the controller in manual, set a
  `Latch` with the cause `on_fault:<controller>` (holding the controller;
  and the output signal for `stop`, or the device where a command stops
  it; the device for `stop_device`), then write the stop under the lock
  already held (`stop_locked`: a blocking device's writer is handed it and
  not waited for), and raise an `on_fault` event. A latch on the store's
  `latch` table survives a restart; `Stopping.attach` re-applies it.

The one-shot runs inside the runner. If the process is stuck or dead,
nothing is released: no bound on a freeze is kept outside flyball.

## The feedforward

The feedforward is the open-loop guess: the output value, in the
**output's** unit, that ought to hold the setpoint, which is in the
**measured** signal's. The law corrects the rest, so its gains are in
output units per measured unit (watts
per °C on a bare heater; °C per °C on a packaged controller that itself
takes a temperature). Feedforwards are typed and self-describing like laws
(`Feedforward` in `flyball.model.feedforward`; subclassing generates the
config, and `control/configs.py` registers the built-in tags on a
`Catalogs`), and a rig file names one per controller:

| type | `output =` | for |
| --- | --- | --- |
| `identity` | `setpoint` | an output that takes the measured unit; the default when the units agree |
| `none` | `0` | a bare output under PID; the default when they differ |
| `affine` | `gain · setpoint + bias [+ rate_gain · rate]` | a plant that is linear near one point |
| `table` | interpolated `(setpoint, output)` points, flat past the ends`[+ rate_gain · rate]` | a static curve measured at commissioning |

`Controller.__init__` refuses `feedforward=Setpoint()` when the units
differ (`ConflictError`): handing a heater "300" meaning °C when it takes
watts would run happily and do nonsense. Without a units mismatch to force
`none`, and with none given, `Setpoint()` is built automatically.

### The setpoint's rate

`rate` is `dSP/dt`, in the measured unit per *second*: the controller asks
the reference for it (`Controller.rate_at`, `SetpointGenerator.rate`),
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
  law: { type: pi, kp: 100, ki: 0.15, tt_s: 30 }
  feedforward:
    type: table
    rate_gain: 3000
    points: [[20, 0], [100, 125.4], [200, 289.4], ...]
  is_default: true
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

A `SetpointGenerator` is a function of *absolute* time, evaluated exactly at
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
| `COLD` | zero, law reset | cold start |
| `CARRY` | what the law already held | keep the offset |
| `TRACK` | what holds the delivered output | bumpless |

`held` — what the output was last actually committed to (`self.expected`),
or, if nothing has been committed yet, last asked (`self.output`) — is
captured at the very top of `regulate()`, before the aim moves. For anything
but `NONE`, the law is cleared next (`clear_law`, which also moves `offset_ns`
to the handover instant). Then, unless there is no reading yet or `transfer
is COLD` (both cold-start straight to `correction = 0.0`, with no call to
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
`(last_applied − last_raw) * (1 − exp(−dt / tt_s))`, where `last_raw` is the
raw (unclamped) correction the law itself computed last step. That is the
continuous law `dI/dt = (last_applied − last_raw) / tt_s` integrated exactly
over the step, so the gap closes by at most all of it: the output never
crosses what was applied, however long `dt` is. (The forward-Euler form it
replaced, `* dt / tt_s`, overshot once `dt > tt_s` and diverged past
`2·tt_s`: a long step off a railed output swung it far past the rail.) An output whose `write` returns `None` — unwired, or a commit
still pending inside a delivery — reports no `delivered_correction`, so that
tick gets no anti-windup term rather than a wrong one. `tt_s` omitted or 0
(or `ki` 0) turns this off outright: a reasonable `tt_s` is about `Ti` (`kp/ki`), or
`√(Ti·Td)` once a derivative term also acts.

`smith`'s own internal model is driven by the same `last_applied` when it is
given, in place of the law's own last output — a clamp or a deferred commit
downstream must not leave the model believing more correction reached the
plant than really did.

## Mode versus permission

`mode` says what the controller is doing. Whether a caller is *allowed* to
change it is a separate question the controller does not answer;
`flyball.foundation.resource` holds a claim graph for that.
