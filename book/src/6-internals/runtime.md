# Runtime

What the equipment *is* and what it is *doing*, kept apart.

## The rig

`Rig` owns the clock, the devices by name, the controllers between their
signals, the polling, the recorder and the tuning registry. It has no
opinion about what any controller regulates.

A delivery ([on_samples][flyball.rig.rig.Rig.on_samples]) runs under the
rig's lock, in this order, for every sample in the batch — a poll, a push, or
a fresh read may deliver several at once:

1. **Every reading lands in `latest`**, whether or not it publishes, and the
   last `RECENT_READINGS` (60) per signal are kept for a stat on request.
2. **Devices with a bound input are touched.** A device with an `Input`
   bound to a signal (`inputs: { dry: hum_sensors.dry.humidity }`) is added
   to the delivery's touched set for every reading on that exact signal; a
   device bound to a whole node is touched by any sample carrying something
   under it, but only once it is `published()` — a subscriber hears only
   what publishes, never a fresh read of an `R`-only setting, which is for
   whoever asked for it. There is no callback: a touched device reads the
   value itself (`self.<input>.value`, from the router) when the rig calls
   its `commit`, in step 4.
3. **Controllers whose measured signal is in the delivery tick.** For every reading
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
   the rig fills in each staged demand's state from what the driver
   pushed (or the committed value, if it pushed nothing). A blocking
   device's commit runs on its own `Writer` thread instead, and its states
   arrive later through `Rig.written`.
5. **The recorder goes last**, so it sees what the whole delivery produced:
   `recorder.record(published, ticks, states, time_ns=...)` — every sample
   that had something published, every controller tick, every write state
   from this delivery's commits.

Streamed samples (`Sample.published()`) and controller states are pushed to
`rig.samples`/`rig.controller_states` (both `Latest` cells) only while
someone is watching, so an idle server costs the delivery nothing beyond the
dict stores above.

A sample's keys must be bound, readable signals under its node, and there
must be at least one — anything else is a driver bug, refused (`ValueError`)
before any of the delivery runs.

## Rig.write: validating a write before anything is recorded

[Rig.write][flyball.rig.rig.Rig.write] puts one or more values on `W`
signals under one node, as a single demand, and validates the whole thing
before touching anything:

1. Every key resolves under the node — a bound `Signal`, or a name relative
   to it, dotted for a namespace (`AddressNotFoundError` if not; `ValueError`
   if a signal is named twice).
2. Every resolved signal is `W` (`ConflictError` otherwise), and not driven
   by an *active* controller other than the one making the demand — a
   controller may re-demand its own target, and a controller in manual
   leaves its target to direct demands; anything else attempting to move
   a regulating controller's target gets "is driven by controller ...:
   set its reference, put it in manual, or detach it". The same rule
   refuses a command with a `mode` or a linked argument, unless it
   `interrupts`.
3. Each value is clamped to the signal's `limits` — numbers, or a reference
   to another signal of the same device, resolved live — and the original
   value kept (as `_requested`) only where the clamp changed it — that is
   what `WriteState.requested` reports later. A reference with no value yet,
   or a non-finite one (NaN, inf: a NaN bound drops its side of the
   clamp, `min(max(-50, nan), 100)` is `-50`), fails closed (`Signal.clamp` raises `LimitNotKnownError`, a
   `NotReadyError`): a manual demand is refused before anything is
   applied; a controller's demand is *held* — `write()` returns `{}`,
   nothing is applied, as for a stale source — and the rig holds a
   `limit_unknown` condition (`warning`) on the controller: raised on
   entering the hold and cleared on the first write after it, not an event
   per step. Refusing rather than clamping to the known end is
   deliberate: in `(0, max_flow)` or `(dry_supply, wet_supply)` the end
   that is not known yet is the one that protects the hardware.

Only once all of this holds does anything happen: `device.apply` (or, for a
blocking device, `writer.apply`) is called once per signal, under the rig's
lock. A manual demand — `by=None`, or one made from outside a delivery — is
committed at once and its states returned; a controller's demand made
*inside* a delivery is folded into that delivery's own commit at the end
(the device is just added to `touched`), and `write()` returns nothing —
the state arrives through the delivery's normal path instead.

## Rig.resolve: addresses parsed once

[Rig.resolve][flyball.rig.rig.Rig.resolve] is the *only* place an
address string is parsed: it splits the device name off the front, looks
the device up, and walks the rest with `Node.find`. Everything below —
readings, samples, demands, controllers — carries the bound `Node`/`Signal`
objects it returns, never a string. `rig.read(target, fresh=False)`
overloads on what `target` is: a `Signal` gives a `Reading`, an atomic
`Node` gives a `Sample`, anything else gives an `Iterator[Sample]`; a
sequence of targets gives a list of results in address order, and `fresh`
reads the hardware first (one `device.read()` per device that owns a
target, on the node they share or the device's root) before answering from
what was just delivered. The fresh read runs off the rig lock, under the
device's `read_lock`, which the poller takes around each `read` too, so reads
of one device never overlap; a fresh read waits `FRESH_READ_WAIT_S` (5 s) for
one in flight and is then refused (409). Only its delivery takes the rig
lock, and what a device removed meanwhile read is dropped.

## Polling

`flyball.rig.polling.Polling` runs each device's `read` on its own
period: [poll_period][flyball.rig.polling.poll_period] is the smallest
`poll_s` over the device's *published* signals (each signal's `poll_s` is
already the nearest one up the tree — its own, else its namespace's, else
the device's), so a device with nothing on a period is never polled at all.
`Rig.start_polling` calls it after a device is added. One `PeriodicLoop`
thread per polled device calls `Polling._read`, which calls `device.read`
and hands whatever comes back to
[delivered][flyball.rig.polling.Polling.delivered] — the same path a
push or a fresh read uses — which runs `rig.on_samples` under the rig's
lock and notes the run (`last_read_ns`) in `Polling.runs`.

What goes wrong is a **condition** in `rig.conditions`
([Conditions][flyball.foundation.device.conditions.Conditions]), keyed by
the object it is true of (a `Device`, a `Signal`, a `Controller`, the rig)
and its code. `set` raises it -- an event with `edge: raised` -- only when
it was not held; setting it again updates its message and nothing else.
`clear` ends it -- `edge: cleared`, with `details.duration_s` -- only when
it was held. Removing a device clears what it and its signals held;
detaching a controller clears what it held. The edges are recorded and
streamed like any event (under the store's own lock, so in order), and
in-process subscribers (`rig.conditions.subscribe`) hear each one on the
store's own thread, never under the rig's lock.

Only `read` itself can put a device offline: a raised exception raises an
`offline` condition, and the device's loop stops itself until `restart`
(which clears it), or until a command on it succeeds (`Polling.revive`,
called by the command route and by a program's `command` step alike). A
controller whose law raises is kept to itself: a `step_failed` condition on
the controller, raised on the first failure and cleared when it steps
again, its mode left as it was, and every other controller, commit,
reading and the recorder carry on. A device whose `commit` raises is
likewise kept to itself: its demands are dropped rather than left staged,
and a `commit_failed` condition names it, raised once per outage and
cleared when a commit succeeds again; the other commits and the recorder
carry on. Any other failure *downstream* of the
read — an observer, the recorder — is the rig's, not the read's: a
`delivery_failed` event, and the device's samples are still noted as read. After a gap in its readings
longer than three usual intervals (an outage), a controller's next step
counts as one ordinary step, not the whole gap.
A poll whose `read` takes longer than its period does not resynchronise or
catch up; it just runs again next period. Only the driver's `read` is timed
(`DeviceRun.read_s`), not the lock wait or the delivery after it, and each
over-period read counts in `DeviceRun.missed`. The `slow` condition is
de-flapped: raised after three reads in a row over the period, cleared
after five in a row at or under 0.8 of it, and a read in between starts
both counts again -- one `raised` and one `cleared` per spell, however the
reads wander inside it.

## Threads and the lock

The rule: nothing that can block on a device or a disk runs under the rig
lock, and nothing that can raise for one device's reasons runs on another
device's thread. The delivery path — readings, observers, a controller's
tick — is arithmetic under the lock. What leaves it:

| work | where it runs |
| --- | --- |
| a blocking device's `commit` (`Device.blocking = True`: SCPI, Modbus, QCoDeS, PyMeasure) | a `Writer` thread per device; `rig.written` delivers its states, publishes and records them once the write completes. A commit or a `rig.written` that raises is a `write_failed` condition and event, never the end of the thread |
| the recorder's writes | the recorder's own thread, every `flush_s`; a store that fails ends the recording with a `recording_failed` event and control is unaffected |
| a device's `read` | the poll loop's thread, or the fresh reader's (`rig.read(..., fresh=True)`), under the device's `read_lock` and never the rig's; the delivery after it takes the rig's. A fresh read asked for by a caller already holding the rig lock is refused |
| a simulated device's `commit` | in the delivery — it is arithmetic, and a stepped clock stays deterministic |
| a long command (`@command(long=True)`: `dosing_pump.dispense`, `stepper.move`) | the caller's thread, off the lock: `Rig.run_command` makes its checks and claims the device's one long-command slot under it (a second is refused), runs the method without it, and takes it back to push `mode`, the linked readings and `last.<command>` and to commit. The method waits with `Device.wait`, on the rig's clock and an event `Device.cancel` sets, so the device's `stop` (a short command, under the lock) ends it at once. A caller already holding the lock is refused rather than made to wait under it; a program's `command` step runs without it (`Step.locked = False`). Removing the device, or closing the rig, cancels it |

`rig.close()` stops all of it: polling, writers, recording, then closes the
rig's links. It waits for reads in progress for `STOP_JOIN_S` (2 s) in
total; a poll thread still in its driver's `read` after that is abandoned
-- it is a daemon thread -- and logged once by device name. A link whose
`close` raises is logged and skipped; the rest still close.

`rig.remove_device` waits for neither thread. It runs under the lock, which
a read or a write in flight needs in order to report, so it stops the
device's poll loop and writer without joining them. Each finishes on its
own thread, finds under the lock that its device is gone (by identity: a
device re-added under the same name is another one) and drops what it read
or wrote.

The server's async routes answer on the event loop and do not take the lock,
which a delivery may hold for a bus transaction: they iterate a
`list(...)` snapshot of the rig's dicts, so a device or controller added
meanwhile is not an error. `Rig.document` and `attach_controller` take it.

## Controllers

`Controllers` indexes by the output's address — a demand has at most one
controller, so the controller *is* named by what it drives — and by
measured signal, for the tick (`Controllers.find(signal)`). The `default` flag is
what a command or program step means when it names no controller.
`rig.attach_controller(output, measured, law=..., feedforward=..., default=...)`
builds one and wires its `write` callback to `rig.write`; `detach_controller`
takes it off its output (left in manual, its last value held) so a manual
demand may drive the output again.

## Triggers

Anything a program waits on — a prompt, a settle test, a timed wait — is a
[Trigger][flyball.foundation.router.trigger.Trigger] registered by name in
`rig.triggers` (`flyball.rig.triggers.Triggers`) for as long as the activity
lasts. `fire` settles it as met; `interrupt` cancels it. Outcomes are pushed
through `Latest` as they settle, from whichever thread settles them; on the
wire these are "activities" (`/api/activities`, `/ws/activities`).

## The programmer

Applies commands in order and waits where a step says to wait. A `Program`
is data. A `Step` is a frozen dataclass that self-registers by tag and
derives its wire model from its constructor. An `Activity` is the ongoing
part of a command: the rig drives it, the programmer owns its lifetime, and
attach/detach bracket the wait so teardown is one `finally` reached by
completion, failure and cancellation alike. Steps that name a controller
(`regulate`, `ramp`, `wait`, `settle`, `manual`) still carry a field called
`loop` in the Python dataclasses (`Regulate.loop`, `Ramp.loop`, ...) — the
*concept* is a controller and the class is `Controller`, but the field takes
a controller's target address (or a list, or `None` for the default), not a
device name; it was not worth renaming.

Locking: the programmer's lock is always the inner lock. Applying a step
takes the rig's lock, then the programmer's; nothing takes them the other
way round, and no thread is joined under either.

`start` applies the first step on the calling thread — so an unapplicable
command raises there — and hands the rest to a worker. `run` blocks. `join`
waits. `cancel` (a person) and `interrupt(reason)` (the engine: a stop,
a shutdown) end whatever is running and wait for the worker to
unwind.

## The recorder

Not an observer: it wants the whole delivery, after the controllers have
ticked and the touched devices have committed. Which signals and
controllers it records is decided once, at construction (the rig defaults
to every signal that publishes or is written, on every device, and every
controller). The mechanism — buffering, the flush thread, what a signal's
access decides is recorded — is in [Storage](db.md#recording-a-session).
