# Writing an actuator

A device written here is named in a rig file through its config class --
[Config and build](config.md) -- and then appears beside the built-in ones
in [Supported drivers](../../2-config/devices/drivers.md), with the envelope
([Devices](../../2-config/devices/index.md)) around it.
An actuator is a `Committable` device: it declares at least one `Demand`
signal (`RPW`, or `RW` — a setting re-set by a command sits beside it).
The minimum is a class, a `Demand` descriptor, and a `write_signal`:

```python
--8<-- "oven.py:heater"
```

## The write side: `apply`, `commit`

Writes are two-phase, the mirror of a sample:

1. `apply(signal, time_ns, value)` — records one value; no I/O. The rig has
   already validated the whole demand (names resolve to `W` signals under
   the node, units convert, no signal another controller's, clamped to
   `limits`) and fans it out one signal at a time. The default stores into
   `self.staged`; most drivers need not override it.
2. `commit(time_ns) -> None` — pushes everything recorded since the last
   commit to hardware **once**. The rig calls it at the end of every
   delivery that touched the device — a demand applied, or one of its
   `Input` signals landing — and immediately after a manual demand. The
   default writes each staged value through `write_signal`; a composite
   device overrides `commit` itself to do arithmetic across everything
   staged and everything it reads from its inputs (`self.<input>.value`) —
   see the blender in [`examples/humidity`](../../0-overview/examples.md).

`commit` returns nothing: the rig reports each staged demand as the
readback the driver pushed (`signal.push(value, time_ns)`), or the
committed value if it pushed nothing itself; a demand that railed has its
`signal.at_limit` set before `commit` returns. `write_signal(signal,
value)` is the simple case: *put one committed value on the hardware*.
`oven.py`'s `Heater` only needs this one method — the default `commit`
calls it once per staged signal.

A demand `commit` never looks at — not through `self.staged` (walking it,
`[]`, `get`, `pop`, `in`) nor `signal.staged` — was not set: the rig does
not echo it as a reading, reports it with the reading unchanged and the
demand as `requested`, and raises a `demand_ignored` event once until one
is read again. A composite that drives from its target and has no use for a
line demand in its present mode gets exactly this for that demand. If
`commit` raises, the device's staged demands are dropped (not sent with a
later commit) and a `commit_failed` event names it; the rest of the
delivery goes on.

There is no dirty flag to maintain in a driver: the rig tracks which
devices a delivery touched and calls `commit` once per device, however many
signals on it changed.

## Limits and controllers

A `W` signal's `limits` — numbers on the descriptor, or a reference to
another signal of the same device (`limits=(0.0, max_flow_config)`,
resolved live) — are enforced by the rig before `apply` is ever called: a
demand outside them is clamped, and the committed state's `at_limit` says
which rail it landed on. `signal.limits` always gives the effective
numbers, whichever way they were declared, and `None` while a referenced
signal has no value yet or a non-finite one (NaN, inf). That case fails closed: the rig clamps through
`signal.clamp(value)`, which raises `LimitNotKnownError` (a
`NotReadyError`, 503 over HTTP) rather than let the demand through --
even when the other end is a number, because the unknown end is usually
the one that matters. A manual demand or a command's linked argument is
refused whole; a controller's write is held (nothing applied) with a
`limit_unknown` event, and `limit_known` once the bound reads a finite value. A driver
whose reference should never block a demand gives that signal an
`initial` value. A controller drives at most one
writable signal; the signal knows which one, so the committed state's
`controller` names it and a manual demand against a controlled signal is
refused.

## Demand, readout and setting

A device declares what each of its signals is with a **role**: `Demand`
(settable, `RPW`; the only thing a controller drives), `Readout` (produced
by the device, never written from outside, `RP`), `Setting` (re-set by a
command, `RP`) and `ConfigSignal` (effective at build, `R`). On the class a
descriptor is its spec; on an instance it is the bound signal. Checked on
subclassing: pydantic must be able to describe every `vtype`.

```python
--8<-- "device.py:heater"
```

- **`demand`** has no command of its own, so the rig synthesises
  `set_demand`: `PUT /api/signals/{address}`, a program `set` step and a
  controller's write all go through that one path.
- **`power`** is produced by `read`, never set.
- **`limit`** is a `Setting`: shown on the wire, but changed only by the
  command that re-sets it — see below.
- **`config`** returns what the device was built from — see
  [Config and build](config.md).

```python
--8<-- "device.py:heater-read-commit"
```

`commit` reads what was just applied from `self.demand.staged`, not
`self.demand.value` — the rig has not yet pushed the reading when `commit`
runs; a driver that needs the value again later reads `.value` instead,
once it has been committed.

## Commands

A method marked `@command` is an action the device offers. Its signature
*is* the command: the server derives the request model from the
parameters, the CLI derives flags, a program derives a `command` step. An
argument is a value for one of the device's demands when it is **named
like the descriptor** — `def set_flows(self, dry_flow: Flow, wet_flow:
Flow)` links `dry_flow` to `self.dry_flow` with no annotation at all — or,
when the parameter must be called something else, when it is annotated
`Annotated[<type>, <descriptor>]` (`dry: Annotated[Flow, dry_flow]`; legal
in the class body because the descriptor's name is already bound there).
Either way it is filled from the demand's current value when left out,
clamped to its effective limits (the command is refused, not run, while
one of them is not known yet), and shown in the schema with the
demand's address, unit and limits:

```python
--8<-- "device.py:heater-command"
```

Every parameter and the return type must be describable by pydantic; this
is checked when the class is defined, not on the first request. The tag
defaults to the method name; `@command(tag="off")` overrides it, and a
command needs a docstring. `schema` is reserved as a route segment.

A command with a `mode` or a linked argument changes what drives the
device, so it is refused while a controller drives one of its demands —
unless `interrupts=True`, which puts the controller in manual first (an
`interrupted` event) and runs anyway.

## Conditions

Something true of the device *now* — railed, offline, overdriven — is a
`Condition`, pushed onto the base class's own `conditions` output:

```python
self.conditions.push((Condition("railed", Level.WARNING, "at the power limit", time_ns),))
```

It appears while it holds and is gone when it clears (push `()`) — a client
joining late sees the present, not a log. `kind` is stable and
machine-readable; `level` uses `logging`'s numbers.

For a moment rather than a condition — a demand clamped, a retry that
worked — a device with a rig in hand records an event instead:

```python
rig.event(Level.WARNING, "device", self.name, "clamped", f"{demand} limited to {limit}")
```

The rig keeps the last few hundred, streams them on `/ws/events`, and
writes them to the session when recording. The rig raises its own: a device
going offline or reading slowly, a program step failing or an activity
timing out.

## What you get

Once attached to a rig (`rig.add_device(heater)`), with no further code:

```
GET  /api/devices/heater                     the signal tree, conditions, readable/writable
GET  /api/devices/heater/schema              the config schema, every signal's and command's
PUT  /api/devices/heater/write              {"demand": 120.0}
POST /api/devices/heater/commands/set_limit  {"fraction": 0.5}

flyball view heater
flyball invoke heater set_limit fraction=0.5
```

and, on `/ws/samples`, a sample per commit carrying each demand's readback
with its write record (`requested`, `at_limit`, `controller`) beside it,
as well as anything it publishes.

## Checklist

- `write_signal` (or `commit`, for a composite device) does the I/O; `apply`
  only records.
- `commit` returns nothing; push a readback (`signal.push(...)`) if the
  committed value differs from the demand, and set `signal.at_limit` if it
  railed. Read every demand it acts on from `staged`: one it never reads is
  reported as `demand_ignored`.
- Anything slow is in `commit`, not `apply` — mark `blocking = True` if
  `commit` may wait on a bus, so the rig runs it on a thread of its own.
- Conditions cover every way the device can fail to do what it was asked.
