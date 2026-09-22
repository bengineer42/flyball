# Assembling a rig

A **rig** owns the clock, every device by name, the controllers between
their signals, the polling, and the recorder. It has no opinion about what
any controller regulates. Assembly is a handful of calls:

```python
--8<-- "oven.py:46:66"
```

## Devices

`rig.add_device(device)` makes it reachable by name — one namespace
rig-wide; a second device with the same name is a `ConflictError`.
`rig.start_polling(device)` polls it on the smallest `poll_s` over its
publishing signals (`None` anywhere: never polled). `rig.read(node,
fresh=True)` reads once, now, on the calling thread — the way to drive a
rig by hand with a stepped clock, as `oven.py`'s `__main__` block does.

## Controllers

```python
rig.attach_controller(heater.signals["demand"], probe.signals["temperature"], law=PI(kp=0.5, ki=0.05), default=True)
```

binds one writable signal to one publishing signal through a law (and an
optional feedforward), and registers it under the target's address — a
controller is *named by what it drives*. A writable signal has at most one
controller: a second `attach_controller` on the same target or source
raises. `default=True` makes it what a command means when it names none;
`rig.controllers.resolve(name | None)` looks one up.

`law` may be a built `ControlLaw`, its config, or the name of a tuning
already registered on the rig (`rig.tunings`).

## A delivery

When a device delivers — a poll, a push, or a fresh read —
`rig.on_samples(samples)` runs under the rig's lock:

1. Every reading lands in `rig.latest`; only signals that publish go on to
   `rig.samples` and the recorder.
2. Devices with a bound `Input` on one of the signals are added to the
   delivery's touched set — there is no callback; a touched device reads
   the value itself (`self.<input>.value`) when its `commit` runs, in step 4.
3. Controllers whose source is in the delivery tick: the law steps, and the
   demand is written to the target.
4. Every device touched — by a tick's write or by a bound input landing —
   has `commit` called once, however many signals on it changed.
5. The recorder, if any, is given the published samples, the ticks, and the
   write states.

Nothing is reordered: samples reach observers, controllers and the recorder
in the order the device delivered them. See
[The delivery loop](../6-internals/runtime.md) for the full sequence.

## Recording

```python
from flyball.record import SqliteStore
store = SqliteStore("run.db")
rig.start_recording(store, **session_fields)
...
rig.stop_recording()
```

Opens a session and records every signal that publishes or is written, and
every controller, from the next delivery on — pass `signals=`/
`controllers=` to record a subset instead. Deliveries are buffered and
written in one transaction per interval, so a controller at any rate pays
one list append per tick. See [Storage](../6-internals/db.md).

## Serving

```python
--8<-- "serve.py:1:15"
```

`set_rig` attaches the rig to the FastAPI app and the observer that feeds
the websockets. Then any ASGI server runs `flyball.interfaces.server:app`.
[The runner](../1-running/runner/index.md) covers running it for real.

## From a file

Everything above, for the generic and registered devices, is a file:

```python
from flyball.runtime.config import load_rig
rig = load_rig("rig.yaml")
```

Links are built first, then devices (and polled, on their period), then
controllers. [Integrations](../5-integrations/index.md) has a
complete file; [Rig file schema](../7-reference/rig-file.md) every field.

## Clocks

A rig has one clock; every controller, polled device and signal reads it.
`Clock()` is wall time. `flyball_sim.SteppedClock` only moves when told to,
so a test ticks a rig at exact instants with no sleeping — but anything
scheduled on real time (a polled device) still runs on real time, so drive
such a rig with `rig.read(node, fresh=True)` by hand instead, as `oven.py`
does with `period=None`.
