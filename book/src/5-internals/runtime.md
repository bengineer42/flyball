# Runtime

What the equipment *is* and what it is *doing*, kept apart.

## The rig

`Rig` owns the clock, the devices by name, the controllers between their
signals, the polling, the recorder and the tuning registry. It has no
opinion about what any controller regulates.

A delivery ([on_samples][flyball.runtime.rig.Rig.on_samples]) runs under the
rig's lock, in this order, for every sample in the batch — a poll, a push, or
a fresh read may deliver several at once:

1. **Every reading lands in `latest`**, whether or not it publishes, and the
   last `RECENT_READINGS` (60) per signal are kept for a stat on request.
2. **Devices with a bound input are touched.** A device with an `Input`
   bound to a signal (`bound: { dry: hum_sensors.dry.humidity }`) is added
   to the delivery's touched set for every reading on that exact signal; a
   device bound to a whole node is touched by any sample carrying something
   under it, but only once it is `published()` — a subscriber hears only
   what publishes, never a fresh read of an `R`-only setting, which is for
   whoever asked for it. There is no callback: a touched device reads the
   value itself (`self.<input>.value`, from the router) when the rig calls
   its `commit`, in step 4.
3. **Controllers whose source is in the delivery tick.** For every reading
   whose signal a controller regulates (`self.controllers.find(signal)`),
   the reading is queued; after every sample in the batch has been walked,
   every queued `(controller, reading)` pair calls
   `controller.on_reading(reading)`. Ticking happens after every touched
   device is known, not per-signal as readings arrive, so a device's commit
   never sees a stale controller state.
4. **One commit per device touched.** `Rig._commit` calls `device.commit(time_ns)`
   once for every device the delivery touched — by a bound input landing
   or by a controller's write — filling in what the rig knows (the
   value requested before clamping, and which controller drives the
   signal) that the device itself cannot know. `commit` returns nothing:
   the rig fills in each pending demand's state from what the driver
   pushed (or the committed value, if it pushed nothing). A blocking
   device's commit runs on its own `Writer` thread instead, and its states
   arrive later through `Rig.written`.
5. **The recorder goes last**, so it sees what the whole delivery produced:
   `recorder.record(published, ticks, states, time_ns=...)` — every sample
   that had something publishing, every controller tick, every write state
   from this delivery's commits.

Streamed samples (`Sample.published()`) and controller states are pushed to
`rig.samples`/`rig.controller_states` (both `Latest` cells) only while
someone is watching, so an idle server costs the delivery nothing beyond the
dict stores above.

A sample's keys must be bound, readable signals under its node, and there
must be at least one — anything else is a driver bug, refused (`ValueError`)
before any of the delivery runs.

## Rig.demand: validating a write before anything is recorded

[Rig.demand][flyball.runtime.rig.Rig.demand] puts one or more values on `W`
signals under one node, as a single demand, and validates the whole thing
before touching anything:

1. Every key resolves under the node — a bound `Signal`, or a name relative
   to it, dotted for a namespace (`AddressNotFoundError` if not; `ValueError`
   if a signal is named twice).
2. Every resolved signal is `W` (`ConflictError` otherwise), and not driven
   by a *different* controller than the one making the demand — a
   controller may re-demand its own target; anything else attempting to
   move a controller's target gets "is driven by controller ...: set its
   reference, or detach it".
3. Each value is clamped to the signal's `limits` — numbers, or a reference
   to another signal of the same device, resolved live — and the original
   value kept (as `_requested`) only where the clamp changed it — that is
   what `WriteState.requested` reports later.

Only once all of this holds does anything happen: `device.apply` (or, for a
blocking device, `writer.apply`) is called once per signal, under the rig's
lock. A manual demand — `by=None`, or one made from outside a delivery — is
committed at once and its states returned; a controller's demand made
*inside* a delivery is folded into that delivery's own commit at the end
(the device is just added to `touched`), and `demand()` returns nothing —
the state arrives through the delivery's normal path instead.

## Rig.resolve: addresses parsed once

[Rig.resolve][flyball.runtime.rig.Rig.resolve] is the *only* place an
address string is parsed: it splits the device name off the front, looks
the device up, and walks the rest with `Node.find`. Everything below —
readings, samples, demands, controllers — carries the bound `Node`/`Signal`
objects it returns, never a string. `rig.read(target, fresh=False)`
overloads on what `target` is: a `Signal` gives a `Reading`, an atomic
`Node` gives a `Sample`, anything else gives an `Iterator[Sample]`; a
sequence of targets gives a list of results in address order, and `fresh`
reads the hardware first (one `device.read()` per device that owns a
target, on the node they share or the device's root) before answering from
what was just delivered.

## Polling

`flyball.runtime.polling.Polling` runs each device's `read` on its own
period: [poll_period][flyball.runtime.polling.poll_period] is the smallest
`poll_s` over the device's *publishing* signals (each signal's `poll_s` is
already the nearest one up the tree — its own, else its namespace's, else
the device's), so a device with nothing on a period is never polled at all.
`Rig.start_polling` calls it after a device is added. One `PeriodicLoop`
thread per polled device calls `Polling._read`, which calls `device.read`
and hands whatever comes back to
[delivered][flyball.runtime.polling.Polling.delivered] — the same path a
push or a fresh read uses — which runs `rig.on_samples` under the rig's
lock and notes the run (`last_read_ns`, cleared conditions) in `Polling.runs`.

Only `read` itself can put a device offline: a raised exception becomes an
`offline` condition and an `offline` event, and the device's loop stops
itself until `restart`. A failure *downstream* of the read — an observer, a
controller's law, the recorder — is the rig's, not the read's: a
`delivery_failed` event, and the device's samples are still noted as read.
A poll that takes longer than its period logs a `slow` condition but does
not resynchronise or catch up; it just runs again next period.

## Threads and the lock

The rule: nothing that can block on a device or a disk runs under the rig
lock, and nothing that can raise for one device's reasons runs on another
device's thread. The delivery path — readings, observers, a controller's
tick — is arithmetic under the lock. What leaves it:

| work | where it runs |
| --- | --- |
| a blocking device's `commit` (`Device.blocking = True`: SCPI, Modbus, QCoDeS, PyMeasure) | a `Writer` thread per device; `rig.written` delivers its states, publishes and records them once the write completes |
| the recorder's writes | the recorder's own thread, every `flush_s`; a store that fails ends the recording with a `recording_failed` event and control is unaffected |
| a simulated device's `commit` | in the delivery — it is arithmetic, and a stepped clock stays deterministic |

`rig.stop()` stops all of it: polling, writers, recording.

## Controllers

`Controllers` indexes by the target signal's address — a `W` signal has at
most one controller, so the controller *is* named by what it drives — and by
source, for the tick (`Controllers.find(signal)`). The `default` flag is
what a command or program step means when it names no controller.
`rig.attach_controller(target, source, law=..., feedforward=..., default=...)`
builds one and wires its `write` callback to `rig.demand`; `detach_controller`
takes it off its target (left in manual, its last demand held) so a manual
demand may drive the target again.

## Triggers

Anything a program waits on — a prompt, a settle test, a hold — is a
[Trigger][flyball.core.trigger.Trigger] registered by name in
`rig.triggers` (`flyball.runtime.triggers.Triggers`) for as long as the wait
lasts. `fire` settles it as met; `interrupt` cancels it. Outcomes are pushed
through `Latest` as they settle, from whichever thread settles them; on the
wire these are "waits" (`/api/waits`, `/ws/waits`).

## The programmer

Applies commands in order and waits where a step says to wait. A `Program`
is data. A `Command` is a frozen dataclass that self-registers by tag and
derives its wire model from its constructor. An `Activity` is the ongoing
part of a command: the rig drives it, the programmer owns its lifetime, and
attach/detach bracket the wait so teardown is one `finally` reached by
completion, failure and cancellation alike. Steps that name a controller
(`regulate`, `ramp`, `hold`, `arrive`, `manual`) still carry a field called
`loop` in the Python dataclasses (`Regulate.loop`, `Ramp.loop`, ...) — the
*concept* is a controller and the class is `Controller`, but the field takes
a controller's target address (or a list, or `None` for the default), not a
device name; it was not worth renaming.

Locking: the programmer's lock is always the inner lock. Applying a step
takes the rig's lock, then the programmer's; nothing takes them the other
way round, and no thread is joined under either.

`start` applies the first step on the calling thread — so an unapplicable
command raises there — and hands the rest to a worker. `run` blocks. `join`
waits. `interrupt` stops whatever is running and waits for the worker to
unwind.

## The recorder

Not an observer: it wants the whole delivery, after the controllers have
ticked and the touched devices have committed. Which signals and
controllers it records is decided once, at construction (the rig defaults
to every signal that publishes or is written, on every device, and every
controller). The mechanism — buffering, the flush thread, what a signal's
access decides is recorded — is in [Storage](db.md#recording-a-session).
