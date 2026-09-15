# Runtime

What the equipment *is* and what it is *doing*, kept apart.

## The rig

`Rig` owns the clock, the readers, the actuators by name, the loops, the
telemetry cells, the signals, the recorder and the tuning registry. It has no
opinion about what any loop controls.

A delivery (`on_read`) runs under the rig's lock, in this order:

1. Observers subscribed to the sample's source or channels.
2. Each loop whose channel is in the delivery ticks.
3. Every touched sink — each ticked loop's actuator, plus whatever observers
   asked for — is `apply`d once.
4. The recorder.

The recorder goes last so it sees what each tick produced. After a tick, or
after `rig.apply(actuator)` following a command, the actuator's state is
stored in a `Latest` cell if anyone is watching; a tick stores the loop's
state the same way.

## Readers

`Readers` keeps a `ReaderRun` beside each device: period, whether it is
running, when it last delivered, and an `offline` condition when a read
raised. A polled reader runs on a `PeriodicLoop` thread; a pushed reader is
attached so its `emit` lands in `on_read` under the lock. Both paths go
through `delivered`, which also stamps the run.

## Loops

`Loops` indexes by name for people and programs, by channel for the tick.
One loop per channel; the `default` is what a command means when it names
none. A loop's name is its actuator's.

## Signals

Anything a program waits on — a prompt, a settle test, a hold — is
registered by name for as long as the wait lasts. `fire` settles it as met;
`interrupt` cancels it. Outcomes are pushed through a `Latest` as they
settle, from whichever thread settles them.

## The programmer

Applies commands in order and waits where a step says to wait. A `Program`
is data. A `Command` is a frozen dataclass that self-registers by tag and
derives its wire model from its constructor. An `Activity` is the ongoing
part of a command: the rig drives it, the programmer owns its lifetime, and
attach/detach bracket the wait so teardown is one `finally` reached by
completion, failure and cancellation alike.

Locking: the programmer's lock is always the inner lock. Applying a step
takes the rig's lock, then the programmer's; nothing takes them the other
way round, and no thread is joined under either.

`start` applies the first step on the calling thread — so an unapplicable
command raises there — and hands the rest to a worker. `run` blocks. `join`
waits. `interrupt` stops whatever is running and waits for the worker to
unwind.

## The recorder

Not an observer: it wants the whole delivery, after the loops have ticked.
Which sources and loops it records is decided once, at construction.
Deliveries are buffered and written in one transaction every `flush_s`; a
transaction costs milliseconds on an SD card whether it holds one row or a
hundred. `close` writes what is left and ends the session.
