# Assembling a rig

A **rig** owns the clock, the readers, the actuators, the loops, the
telemetry cells, the signals and the recorder. It has no opinion about what
any loop controls. Assembly is four calls:

```python
--8<-- "oven.py:34:49"
```

## Readers

`start_reader(reader)` attaches a reader so its pushed samples reach the rig.
`start_reader(reader, period=1.0)` also polls it every second on a thread.
The thread reads, delivers, and records when it last did; if `read` raises,
the reader is marked `offline` in its run state until the next delivery.

`rig.read(reader)` polls once, now, on the calling thread — the way to drive
a rig by hand with a stepped clock, as `oven.py` does.

## Loops

`attach_loop(channel, actuator, law, default)` builds a loop, registers the
actuator by name, and claims the channel. One loop per channel: a second
claim is a `ConflictError`. The `default` loop is what a command means when
it names none.

`law` may be a built `ControlLaw`, its config, a `Tuning`, or the name of a
tuning already registered on the rig.

## A delivery

When a reader delivers, `rig.on_read` runs under the rig's lock:

1. Observers subscribed to the sample's source or channels hear it.
2. Each loop whose channel is in the delivery ticks on its reading.
3. Every sink touched — each ticked loop's actuator, plus whatever the
   observers asked for — is `apply`d once.
4. The recorder, if any, is given the samples and the ticks.

The recorder goes last so it sees what each tick produced. Nothing is
reordered: samples reach observers, loops and the recorder in the order the
reader delivered them.

## Observers

An observer hears samples or readings from the sources and channels it names,
and can ask for sinks to be applied after it fires:

```python
class Logger(Observer[Reading]):
    name = "logger"
    observes = frozenset({probe[TEMPERATURE]})
    def observe(self, reading: Reading) -> None:
        print(reading.value)

rig.attach_observer(Logger())
```

Settle tests and feedforward sources are observers.

## Recording

```python
from flyball.db import SqliteStore
store = SqliteStore("run.db")
rig.start_recording(store, config={...})
...
rig.stop_recording()
```

Opens a session and records every source seen so far and every loop from the
next delivery on. Deliveries are buffered and written in one transaction
per interval, so a loop at any rate pays one list append per tick. See
[Storage](../5-internals/db.md).

## Serving

```python
--8<-- "serve.py:1:14"
```

`set_rig` attaches the rig to the FastAPI app and the observer that feeds the
websockets. `set_store(store)` does the same for history routes. Then any
ASGI server runs `flyball.server:app`. [The daemon](../3-running/daemon.md)
covers running it for real.

## From a file

Everything above, for the generic devices, is a file:

```python
from flyball.runtime.config import load_rig
rig = load_rig("rig.toml")
```

Links are built first, then readers (and started, with their period), then
actuators, then loops. [Supported equipment](../3-running/equipment.md) has
a complete file; [Rig file schema](../6-reference/rig-file.md) every field.

## Clocks

A rig has one clock; every loop, reader and signal reads it. `Clock()` is
wall time. `flyball.sim.SteppedClock` only moves when told to, so a test
ticks a rig at exact instants with no sleeping — but anything scheduled on
real time (a polled reader) still runs on real time, so drive such a rig with
`rig.read(reader)` by hand instead.
