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
2. Every resolved signal is `W` (`ConflictError` otherwise; for a readback
   demand, `[RP]`, the message names the command whose argument is linked to
   it, else one that declares it in `writes=`), and not driven
   by an *active* controller other than the one making the demand — a
   controller may re-demand its own target, and a controller in manual
   leaves its target to direct demands; anything else attempting to move
   a regulating controller's target gets "is driven by controller ...:
   set its reference, put it in manual, or detach it". The same rule
   refuses a command with a `mode`, a linked demand or `writes=`, unless it
   `interrupts`. `Rig.invoke` checks that before the method runs and puts
   each displaced controller in manual only after the method has returned,
   before the commit and the flush, so a controller cannot write again in
   between; it returns `CommandRun(result, interrupted)` (`run_command`
   returns only `result`).
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

Only `read` itself can put a device offline. `_read` iterates the driver's
`read` into a list, so samples yielded before a raise are delivered all the
same; the raise then goes to `Polling._failed`, which counts it in
`DeviceRun.consecutive_failures` against the device's
[ReadPolicy][flyball.rig.polling.ReadPolicy] -- its entry's `reads:` key by
key over `Polling.defaults` (`runner.reads`, set by `RigConfig.build`),
resolved when its polling starts. Below `fail_after` it is a log line. At
`fail_after` it sets `offline` (raised once; each later failure only
updates its message) and, on that raise, `Rig.device_offline` delivers a
`stale(device_offline)` no-value on the device's read path -- the readouts
and `sensed` demands its polled reads have delivered (`Rig.note_read` keeps
the set as `Polling.delivered` hands samples over), never its settings,
configs or echo demands -- so what follows them fails closed at once. The
loop keeps running: `PeriodicLoop.defer(wait)`
puts its next run `backoff_s[n]` from now, `n` counting retries and the
last wait repeating, and `DeviceRun.next_retry_ns` says when. `defer`
moves one run: on a thread it resets `_next_loop_time`, on a stepped clock
it swaps the periodic schedule for a one-shot that puts the period back
before it runs; either way the rig's clock times it, and `stop` ends the
wait at once, so a removal or `close` does not wait out a 60 s backoff.
The first read that succeeds (`_recovered`) zeroes the count, clears
`next_retry_ns` and clears `offline`, and the loop is on its period again.
A device's `give_up_after_s`, once `offline` has been held that long,
stops the loop from inside (`running: false`) with a `gave_up` event;
`offline` stays. `DeviceRun.reading_since_ns` is set on the rig's clock
while a poll's `read` is in flight, and pushed, so a read that never
returns shows on the runs stream. Each poll also arms a one-shot on the
rig's timers at `max(3·period, 5 s)` ([hung_after_s][flyball.rig.liveness.hung_after_s]);
if that read is still in flight when it comes up, the device holds `hung`
and `Rig.device_hung` delivers `stale(device_hung)` on its read path, as
`device_offline` does. The read returning cancels the one-shot and clears
`hung`.

`restart` stops the loop and starts a new one on the period (its first
read one period later), clearing nothing: `offline` and the count stay
until a read succeeds. A command that succeeds on a device that is offline
or stopped (`Polling.revive`, called by the command route and by a
program's `command` step alike) restarts it the same way. Neither waits on
a read in flight: `restart` is refused (409) while one is
(`Polling.reading_for`), and stops the old loop for at most `STOP_JOIN_S`
before refusing too; `revive` on a device whose poll has been stuck in a
read for longer than its period leaves it alone and says so with a
`not_revived` event on the device. A loop left to finish its read after a
restart neither backs off nor stops the newer loop if that read raises
(`PeriodicLoop.runs_here`). A
controller whose law raises is kept to itself: a `step_failed` condition on
the controller, raised on the first failure and cleared when it steps
again, its mode left as it was, and every other controller, commit,
reading and the recorder carry on. A device whose `commit` raises is
likewise kept to itself: its demands stay staged (`Rig._commit_failed`
keeps `staged`, each `_requested` record and `at_limit`, and forgets only
which the driver read), a `write_failed` condition names it, raised once
per outage and cleared when a commit succeeds again, and `_retry_later`
arms a retry on the rig's timers (`min(poll_s, 5 s)`, doubling to 60 s);
the other commits and the recorder carry on. The retry (`_retry_due`,
under the rig lock) drops what is older than `retry_max_age_s`
(`write_dropped`, the demand added to `_write_lost`) and commits the rest
as a manual demand would. Meanwhile `Rig._writes_failing` gives every echo
demand on the device (with a reading) a `stale(write_failed)` no-value and
remembers the demands of the failed commit in `_write_lost`; the commit
that clears the condition carries the kept values (each a `resent` event),
takes them out of `_write_lost`, and calls `_writes_recovered`, which pushes
each still-stale echo demand's `router.last_usable` value back, except
those still in `_write_lost` (a dropped one: a demand of its own has to
commit first). A blocking device's `Writer` merges a failed write's values
back into its queue (a newer value on a signal wins), and does the rest
through `Rig.writes_failed` / `writes_recovered` / `resent`, which take the
rig lock from the writer thread; its retry calls `Writer.retry`. A sample from any source goes through
the value gate first (`normalised`, in `on_samples`): `None`, NaN and
infinities become `invalid` no-values and a `railed` value its number plus
a `Sample.marks` entry; `router.last_usable` keeps each signal's newest
reading that had a value. Any other failure *downstream* of the
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
| a deadline on the rig clock: a signal's liveness, a hung read's watchdog, a write retry, a fault's wait, a band's `invalid` grace, a controller's re-apply | the rig's [Timers][flyball.foundation.time.timer.Timers] (`rig.timers`, `rig.after`, `rig.every`): one thread, waiting on the rig's clock, for wall time and a scaled sim; on a stepped clock, whoever advances it. Each call is short and takes the rig lock itself if it needs it; one that raises is logged and counted (`rig.timers.errors`), never the end of the thread |
| a long command (`@command(long=True)`: `dosing_pump.dispense`, `stepper.move`) | the caller's thread, off the lock: `Rig.run_command` makes its checks and claims the device's one long-command slot under it (a second is refused), runs the method without it, and takes it back to push `mode`, the linked readings and `last.<command>` and to commit. The method waits with `Device.wait`, on the rig's clock and an event `Device.cancel` sets, so the device's `stop` (a short command, under the lock) ends it at once. A caller already holding the lock is refused rather than made to wait under it; a program's `command` step runs without it (`Step.locked = False`). Removing the device, or closing the rig, cancels it |

`rig.close()` stops all of it: the timers first (nothing armed fires while
the rig comes down; a call in progress is waited on for at most 1 s), then
polling, writers, recording, then closes the rig's links. It waits for reads in progress for `STOP_JOIN_S` (2 s) in
total; a poll thread still in its driver's `read` after that is abandoned
-- it is a daemon thread -- and logged once by device name. A link whose
`close` raises is logged and skipped; the rest still close.

`rig.remove_device` waits for neither thread. It runs under the lock, which
a read or a write in flight needs in order to report, so it stops the
device's poll loop and writer without joining them. Each finishes on its
own thread, finds under the lock that its device is gone (by identity: a
device re-added under the same name is another one) and drops what it read
or wrote.

The server's async routes and websockets answer on the event loop and never
take the lock, which a delivery may hold for a bus transaction: they iterate a
`list(...)` snapshot of the rig's dicts (`Polling.snapshot()` for the runs,
`Controllers.get` for one controller), so a device or controller added or
removed meanwhile is not an error. `/api/health` is lock-free. What must take
the lock (`Rig.document`, `attach_controller`, `recent_readings`) is reached
only from plain `def` routes, on the threadpool. A test fixture
(`tests/conftest.py`) fails any test in which a thread running an event loop
took `rig.lock`, as another does for the store's.

The programmer's lock is never held while the rig's is taken: each step is
applied with the programmer's released, on `start` as on the worker. So the
only nesting is rig, then programmer (an operator's `on_revoke` calls
`Programmer.interrupt` from whoever revoked it, perhaps under the rig's lock),
and it cannot meet the reverse. `cancel`/`interrupt` join the worker for at
most `END_JOIN_S` (5 s), since a caller may hold the rig's lock the worker's
next step needs; a step still running then is a `step_still_running` event,
and the call returns False.

A collection that more than one thread changes is either changed and iterated
under one lock or iterated through a C-level `list(...)` copy (`dict(d)`,
`list(d.items())` do not let another thread in part-way). `Polling` keeps
its dicts (`by_name`, `periodic`, the runs, the `slow` streaks) under a lock
of its own, taken after the rig's and never held across a join, so a run
noted by a poll thread cannot put back one a `stop` just removed; `stop_all`
and `rig.close` walk copies of the loops, writers and links, as a request
may add one while the rig shuts down.

## Timers and liveness

[Timers][flyball.foundation.time.timer.Timers] is the one rig-clock timer:
`after(seconds, fn)` for a one-shot, `every(seconds, fn)` for a periodic
call, each returning a `Timer` to `cancel`. The calls live in one heap;
a thread runs them for a clock that runs by itself, waiting with
`Clock.wait` (so a scaled clock's deadlines come at the scaled time, and
in steps of at most 0.5 s of real time, so a speed change is followed), and
a `SteppedClock` runs them itself, in time order with its polls, through one
call kept on it (`call_later`) at the earliest due time. `SteppedClock`
does not hold its schedule's lock while it runs a call, so a thread holding
the rig's lock may arm a timer while a call waits for that lock. The rig
makes its timers on first use, on the clock it has then; swapping
`rig.clock` rebuilds them. `PeriodicLoop` stays the poll loop -- a device's
read may block, so each device has its own thread -- and its backoff
(`defer`) is not on the timers.

[Liveness][flyball.rig.liveness.Liveness] (`rig.liveness`) keeps one record
per judged signal (a published readout or `sensed` demand, with a
threshold: its `stale_after_s`, else `max(3·poll_s, 5 s)` while polled),
built when its device's polling starts. A delivery notes each arrival
(`received_ns`, stamped on the reading by `Router.note`) and does nothing
else unless the record has no one-shot armed; the one-shot, when it comes
up, re-arms for the true deadline or, under the rig lock, delivers a
`stale` reading on every signal of the device due by then, stamped at that
instant. The reason is the device's (`device_offline`, `device_hung`) if it
holds one, else `never_read`, `last_read` (the device's other signals
still arrive) or `silent`.

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
