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
   inputs' sources landing — and immediately after a manual demand. The
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
`commit` raises, the device's staged demands are kept for its next commit
and retried on the rig's clock, a `write_failed` condition names the
device until a commit succeeds, and the rest of the delivery goes on
([A write that fails](../../2-config/devices/index.md#a-write-that-fails)).
Retrying sends a demand's latest value again: `commit` should set levels,
which a second send does not change.

There is no dirty flag to maintain in a driver: the rig tracks which
devices a delivery touched and calls `commit` once per device, however many
signals on it changed.

## Limits and controllers

A `W` signal's `limits` — numbers on the descriptor (or set on the instance
in `__init__`, `self.dry_flow.set_meta(limits=(0.0, max_flow))`), or a
reference to another signal or an input of the same device
(`limits=(dry_supply, wet_supply)`, resolved live) — are enforced by the rig before `apply` is ever called: a
demand outside them is clamped, and the committed state's `at_limit` says
which rail it landed on. `signal.limits` always gives the
numbers in force, whichever way they were declared, and `None` while a referenced
signal has no value yet or a non-finite one (NaN, inf). That case fails closed: the rig clamps through
`signal.clamp(value)`, which raises `LimitNotKnownError` (a
`NotReadyError`, 503 over HTTP) rather than let the demand through --
even when the other end is a number, because the unknown end is usually
the one that matters. A manual demand or a command's linked argument is
refused whole, naming the bound and its quality (`its limit follows 'dry'
(pending)`); a controller's write is held (nothing applied) with a
`limit_unknown` condition -- `info` while what it follows is only
`pending`, `warning` once it is `stale` or `invalid` -- cleared once the
bound reads a finite value. A driver
whose reference should never block a demand gives that signal an
`initial` value. A controller drives at most one
writable signal; the signal knows which one, so the committed state's
`controller` names it and a manual demand against a controlled signal is
refused.

## Inputs

What a device follows -- another device's signal, or a number -- is an
`Input`, declared in the class body and bound in the rig file
(`inputs: {dry: hum_sensors.dry.humidity, wet: 88.5}`). It has no default:
the rig file gives every declared input an address or a number, or it does
not load.

```python
class Blender(Committable):
    humidities = Namespace("humidities", "Supply humidities")
    dry_supply = humidities.input("dry", "Dry line humidity", HUMIDITY)
    wet_supply = humidities.input("wet", "Wet line humidity", HUMIDITY)
    humidity = Demand("humidity", "Humidity", HUMIDITY, limits=(dry_supply, wet_supply))
    expected = Readout("expected_humidity", "Expected humidity", HUMIDITY)

    def commit(self, time_ns: int) -> None:
        try:
            dry, wet = values_of(self.dry_supply, self.wet_supply)
        except NoValueError as error:
            self.expected.push(error.no_value, time_ns)  # the supply's quality, carried
            return
        except NotReadyError:
            return  # pending: nothing to say yet
        ...
```

- On an instance, `self.dry_supply` is its
  [`InputBinding`](../model.md#inputs): `value` raises `NotReadyError` while
  it is `pending` and `NoValueError` while its source has no value --
  never a stand-in number. A limit may follow it.
- **When it lands.** The rig calls `inputs_changed(time_ns, changed)` in the
  delivery that brought the reading, before the controllers step; a device
  with demands is then committed too, and reads it there. A device with no
  demands that computes an output from an input (a curve, a sum) overrides
  `inputs_changed` and pushes the output; it is delivered in the same chain,
  so a controller measuring it does not lag.
- **An output computed from an input carries its quality**: push the
  `NoValueError`'s `no_value` on it, not a number. `values_of(a, b)` reads
  several and raises the one that ranks first (`stale(device_offline)` over
  `pending` over `invalid`).

## Demand, readout and setting

A device declares what each of its signals is with a **role**: `Demand`
(settable, `RPW`; the only thing a controller drives), `Readout` (produced
by the device, never written from outside, `RP`), `Setting` (re-set by a
command, `RP`). A number the device is built from is not a signal: it is a
config field, or metadata of the signal it bounds -- a pump's maximum flow
is the top of that flow's `limits`, set in `__init__` with
`signal.set_meta(limits=...)`. On the class a descriptor is its spec; on
an instance it is the bound signal. Checked on
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
clamped to its limits (the command is refused, not run, while
one of them is not known yet), and shown in the schema with the
demand's address, unit and limits:

```python
--8<-- "device.py:heater-command"
```

Every parameter and the return type must be describable by pydantic; this
is checked when the class is defined, not on the first request. The command's
name defaults to the method name; `@command(name="off")` overrides it, and a
command needs a docstring. `schema` is reserved as a route segment.

A command with a `mode` or a linked argument changes what drives the
device, so it is refused while a controller drives one of its demands —
unless `interrupts=True`. Then the rig checks everything first, runs the
method, and only once it has succeeded puts the controller in manual (an
`interrupted` event); a command that raises leaves the controller
regulating. The response names each controller it put in manual
(`interrupted: [{controller, was}]`). A long command cannot interrupt
(refused when the class is defined): the controller would fight it while it
waits.

A command that moves a demand without a linked argument — a relay's `on`
and `off`, a PWM channel's `off`, a blender's `set_blend` — says so with
`writes=`, naming the demands by descriptor or path:

```python
@command(writes=(on,))
def off(self) -> None:
    """Switch the line off, whatever was last demanded."""
```

It is then refused while a controller drives the device, like a command
with a `mode`. A command that drives a private child device the rig cannot
see (a dosing pump's motor) names the child (`writes=("pump",)`); that
refuses nothing yet, since no controller can drive the child.

A demand that only a command moves is declared `access=Access.RP`: a
readback. A direct write to it is refused, and the refusal names the
command whose argument is linked to it (failing one, a command that
declares it in `writes=`), and says when that command puts a regulating
controller in manual — `'blender.flows.dry' [rp] is not writable: it is a
readback, moved by the command 'set_flows' (it puts a regulating controller
in manual)`.

A driver reports something that happened once — a blend flow resolved, a
request scaled — with `self.event(code, severity, message, details)`: a
point event, logged and recorded like the rig's own, with nothing held
(compare `set_condition` below).

A command runs under the rig lock, so it must return promptly: nothing
waits while holding it. One that takes time — a dose, a move — is
`@command(long=True)`. The rig makes its checks under the lock, runs the
method off it (polling, control and other commands carry on), and takes
the lock back only to record what ran. Such a command waits with
`self.wait(seconds)`, never `time.sleep`: the wait is in the rig's time
(a scaled or stepped sim clock scales or steps it) and returns `True` at
once when `self.cancel()` is called. The device's `stop` command calls
`self.cancel()` first, so a stop ends the long command straight away (a
rig stop, and the end of a program whose step is running it, cancel it
too); the long command's `finally` switches the hardware off:

```python
@command(long=True)
def dispense(self, volume_ml: float) -> None:
    """Run the pump for the dose, then stop it."""
    try:
        self._run(True)
        stopped = self.wait(volume_ml / self.ml_per_s.value)
    finally:
        self._run(False)

@command(stops=True)
def stop(self) -> None:
    """Stop the pump now: a dose in progress ends."""
    self.cancel()
    self._run(False)
```

A device runs one long command at a time: a second is refused (409)
until the first ends. A long command cannot be run by a caller already
holding the rig lock (refused, 409); a program's `command` step does not
hold it.

## What a stop does to it

A [software stop](../../1-running/runner/access.md#stopping-the-rig), a
shutdown and a controller's `on_fault: stop` apply each device's
[resolved stop](../../2-config/devices/index.md#stop-what-a-stop-writes).
A driver says what that is in one of two ways, or neither.

**A stop command.** `@command(stops=True)` makes a command the device's
stop: a stop runs it instead of writing values, and the rig file's `stop:`
values are refused on the device. At most one per driver (two are refused
when the class is defined). It runs under the rig's lock, so it must
return promptly and cannot be `long`; a running long command has already
been cancelled when it runs. Use it when the device has one action that
stops it whole -- a blender's pumps off together, a DAC's power-down, a
stepper's enable line released. A driver whose stop depends on its config
overrides `stops_by()`, returning the command's name or `None`
(`scpi` returns `stop` only when a `stop_command:` is configured). If the
stop command raises, the stop writes each demand's declared `off` instead
and reports the device `failed`.

**An `off` on a demand.** `SignalSpec(off=...)`, or `off=` on the
descriptor, is the demand's inactive level: what a stop writes when the
rig file says nothing for it.

```python
drive = Demand("drive", "Drive", quantity=DRIVE, limits=(0.0, 1.0), off=0.0)
```

Declare it only where it cannot be wrong. A PWM duty's 0 % is off; a DAC's
0 V is a setpoint for a positioner or a VFD, so `mcp4725` declares none.
Never on an inverted output (whether logical 0 is the load's off depends on
why it was inverted), and never on a span that straddles 0, where one end
is full reverse. `off` is a logical value, before any `invert`, in the
signal's unit. It is written even outside `limits`, which bound regulation,
not switching an output off. `spanned_signal_spec(..., off_at_zero=True)`
(`flyball.hardware.spanned_demand`) declares 0, or `span[0]`, for a spanned
demand, and none across 0; its default is none.

**Neither.** The output is kept: a stop leaves it where it is, energised if
it was, unless the rig file gives it a `stop:` value. That is the right
answer wherever the driver cannot know what off means for the load.

## Conditions

Something true of the device *now* — railed, overdriven, a sensor failed —
is a condition, raised and cleared through the device:

```python
self.set_condition("railed", Severity.WARNING, "at the power limit")
...
self.clear_condition("railed")
```

It is held while it holds and gone when it clears — a client joining late
sees the present in `conditions`, and the event log has one `raised` and
one `cleared` edge for it, not one per call. `code` is stable and
machine-readable; `severity` is `debug`, `info`, `warning` or `error`.
`signal=` puts it on one of the device's signals instead. See
[Conditions](../model.md#conditions).

For a moment rather than a condition — a demand clamped, a retry that
worked — a device with a rig in hand records an event instead:

```python
rig.event(Severity.WARNING, "device", self.name, "clamped", f"{demand} limited to {limit}")
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
