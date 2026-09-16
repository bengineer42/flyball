# Writing an actuator

An actuator is a `Device` whose signals are `W` (or `RW`/`RPW` where a
setting or a readback belongs beside them). The minimum is a class, a
`TREE` with one writable signal, and a `write_signal`:

```python
--8<-- "oven.py:40:50"
```

## The write side: `apply`, `observe`, `commit`

Writes are two-phase, the mirror of a sample:

1. `apply(signal, time_ns, value)` — records one value; no I/O. The rig has
   already validated the whole demand (names resolve to `W` signals under
   the node, units convert, `together` groups complete, no signal another
   controller's, clamped to `limits`) and fans it out one signal at a time.
   The default stores into `self.pending`; most drivers need not override
   it.
2. `observe(reading_or_sample)` — a bound input changed: a signal on
   *another* device this one declared it follows (`bound:` in the rig
   file). Only called on a device with something in `self.bound`.
3. `commit(time_ns) -> {Signal: WriteState}` — pushes everything recorded
   since the last commit to hardware **once**. The rig calls it at the end
   of every delivery that touched the device, and immediately after a
   manual demand. The default writes each pending value through
   `write_signal` and reports a `WriteState` per signal; a composite device
   overrides `commit` itself to do arithmetic across everything pending —
   see the blender in [`examples/humidity`](../examples.md).

`write_signal(signal, value)` is the simple case: *put one committed value
on the hardware*. `oven.py`'s `Heater` only needs this one method — the
default `commit` calls it once per pending signal and reports the write
states.

There is no dirty flag to maintain in a driver: the rig tracks which
devices a delivery touched and calls `commit` once per device, however many
signals on it changed.

## Limits and controllers

A `W` signal's `limits` (declared in `TREE` or overridden in the rig file)
are enforced by the rig before `apply` is ever called — a demand outside
them is clamped, and the committed `WriteState.at_limit` says which rail it
landed on. A controller drives at most one writable signal; the signal
knows which one, so `WriteState.controller` names it and a manual demand
against a controlled signal is refused.

## The three tiers

A device describes itself in config, settings and state. Each is a type
the device declares by annotating a property, checked on subclassing: it
must derive from the right base and pydantic must be able to describe it.

```python
--8<-- "device.py:20:40"
```

- **Config** is a `DriverConfig` (a pydantic model), because it is *what
  builds the device*: it arrives from a file or a request, is validated,
  and `build()`s. Give it a `tag` so a file can select it by name. See
  [Config and build](config.md).
- **Settings** and **state** are frozen dataclasses. Settings change by
  command; state changes every tick or read.

The device returns them from properties:

```python
--8<-- "device.py:58:71"
```

## Commands

A method marked `@command` is an action the device offers. Its signature
*is* the command: the server derives the request model from the
parameters, the CLI derives flags, a program derives a `command` step.

```python
--8<-- "device.py:73:82"
```

Every parameter and the return type must be describable by pydantic; this
is checked when the class is defined, not on the first request. The tag
defaults to the method name; `@command(tag="off")` overrides it. `schema`
is reserved as a route segment.

## Conditions

Something true of the device *now* — railed, offline, overdriven — is a
`Condition` in its state:

```python
--8<-- "device.py:66:71"
```

It appears while it holds and is gone when it clears. A client joining late
sees the present, not a log. `kind` is stable and machine-readable; `level`
uses `logging`'s numbers.

For a moment rather than a state — a demand clamped, a retry that worked —
a device with a rig in hand records an event instead:

```python
rig.event(Level.WARNING, "device", self.name, "clamped", f"{demand} limited to {limit}")
```

The rig keeps the last few hundred, streams them on `/ws/events`, and
writes them to the session when recording. The rig raises its own: a device
going offline or reading slowly, a program step failing or a wait timing
out.

## What you get

Once attached to a rig (`rig.add_device(heater)`), with no further code:

```
GET  /api/devices/heater            the signal tree, config, settings, state
GET  /api/devices/heater/schema     the three schemas and every command's
PUT  /api/devices/heater/demand     {"demand": 120.0}
POST /api/devices/heater/set_limit  {"limit": 0.5}
POST /api/devices/heater/off

flyball heater
flyball heater set_limit 0.5
flyball heater off
```

and one frame per change on `/ws/writes` for its writable signals, plus
`/ws/samples` for anything it also publishes.

## Checklist

- `write_signal` (or `commit`, for a composite device) does the I/O; `apply`
  only records.
- `config`, `settings`, `state` return the declared types.
- Anything slow is in `commit`, not `apply` — mark `blocking = True` if
  `commit` may wait on a bus, so the rig runs it on a thread of its own.
- Conditions cover every way the device can fail to do what it was asked.
