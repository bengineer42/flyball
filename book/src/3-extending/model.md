# The device model

*Not to be confused with the `flyball.model` Python package* (`Catalog` →
`Config` → `Instance`, the type-registration machinery for devices, links,
laws, feedforwards and generators — see [6-internals](../6-internals/index.md)). This
page is about the concepts below: `Device`, `Signal`, `Quantity`.

Flyball's vocabulary is small. A handful of nouns cover everything the rig
is made of, and the rest of the library is arithmetic over them.

## Device

Everything with a name is a **device**: a sensor, a relay, a multi-channel
instrument, a composite like a split-range blender. A device has a tree of
**signals** (namespaces group them into sub-devices), commands, and
**conditions** — what is true of it now (railed, offline, overdriven),
held in the rig's condition store (see [Conditions](#conditions)). There is
no separate reader or actuator class: a device is `Readable` (implements
`read`, produces samples on a schedule) if it polls, `Committable`
(implements `apply`/`commit`) if it has demands, both, or neither — what
falls out of that is which access flags its signals carry.

```python
from flyball.foundation import Quantity
from flyball.foundation.quantities.si import Celsius

TEMPERATURE = Quantity("temperature", Celsius)
```

A **quantity** is what is measured or set, independent of any device: a
name and a unit, nothing else. It is not interned and not process-wide —
two devices may both report `temperature` in °C without sharing an object.
Range, precision and bands live on the *signal*, because two thermocouples
on one rig can differ in all three.

A unit makes its own quantity: `Celsius.quantity()` is
`Quantity("temperature", Celsius)` — the name is the dimension's,
lower-cased (`Watt.quantity()` → `power`, `Hertz.quantity()` → `frequency`).
Give a name to say what is measured rather than what kind of thing it is
(`Celsius.quantity("chamber")`), and you must give one for a unit on a
dimension nobody has named, such as a composed `W/m⁵`: `Unit.quantity()`
raises `ValueError` there.

## Roles

Every signal has a **role**, declared on its descriptor and fixed for the
device's life. The role sets the signal's default access:

| role | access | meaning |
| --- | --- | --- |
| `Role.DEMAND` | `RPW` | settable, with a current value (its readback) that updates — the only thing a controller drives, and only while it is `W` |
| `Role.READOUT` | `RP` | produced by the device, never written from outside: a measurement, a derived value, a mode |
| `Role.SETTING` | `RP` | re-set by a command while the device runs, shown; never a controller's output, even when a driver or the rig file makes it `W` (a [`values`](../2-config/devices/drivers.md#values) device's entries are settings with `RPW`) |

A number a device is built from is not a signal. It is a driver config
field, or metadata of the signal it bounds: a pump's maximum flow is the top
of its flow demand's `limits`, set on the instance in `__init__`
(`self.dry_flow.set_meta(limits=(0.0, max_flow))`).

An **input** is not a role either: it is what the device follows -- another
device's signal, or a number -- bound by the rig (`inputs:`), and not in
this device's tree; an `Input` descriptor is told apart by its type.

### Inputs

On an instance an `Input` descriptor is its
[`InputBinding`][flyball.foundation.device.binding.InputBinding]
(`self.dry_supply`, also `device.bound["dry"]`), one object for the
device's life: the rig resolves the rig file's entry into it at build --
the source `Signal` or `Node`, or the constant -- and a limit that follows
the input holds the same object. It reads through to the source's newest
reading; nothing is copied onto the device:

| on the binding | gives |
| --- | --- |
| `value` | the constant, or the source's value; raises `NotReadyError` while `pending` (unbound, or nothing read yet) and `NoValueError` while the source's reading has none -- its `no_value` is the source's, quality and reason |
| `quality`, `reason`, `at_limit` | the source's now (`pending` before its first reading; a constant is `ok`) |
| `age_s(now_ns=None)` | seconds since the rig received the source's newest reading with a value, on the rig's clock |
| `state()` | all of it in one call: an `InputState` of value, quality, reason, `at_limit`, age, source address or constant, unit |
| `source`, `signal`, `constant`, `address`, `unit` | what it follows |
| `watch(callback)` | a callback on each new reading of the source (a change of quality always arrives as one), on the delivery thread under the rig's lock; returns the detach |

An input has **no default**: a rig file gives every declared input an
address or a number, or it does not load (`Input(..., default=...)` is a
`TypeError`). A device is told its inputs changed through
`inputs_changed(time_ns, changed)`, in the delivery that brought the
readings, before the controllers step -- what it pushes there is delivered
in the same chain, so a controller measuring an output computed from an
input does not lag a delivery -- and a device with demands is then
committed, where it reads them (`self.dry_supply.value`). An output it
computes from an input with no value carries that input's quality: push
`error.no_value` from the `NoValueError`, not a number, and read several
inputs with `values_of(a, b)`, which raises the one that ranks first
(`stale(device_*)`, then `pending`, then another `stale`, then `invalid`).

Anything else that follows a signal holds a binding too: `rig.follow(address,
owner=..., name=...)` makes one (a program step's, a controller's), which it
reads, watches, and gives back with `rig.unbind(binding)`.
`rig.consumers(signal)` answers the reverse: every binding that follows the
signal, or a namespace above it.

Structure is declared once, as descriptors in the class body (`Namespace`,
`Demand`, `Readout`, `Setting`, `Input`), or built from config
in `__init__` with the same factories and bound with `Device.bind`.
`tags={"line": "dry"}` on a descriptor groups it along a second axis across
the tree, orthogonal to the namespace (`flows.dry` and `efforts.dry` share
`line: dry`). On the class a descriptor is
its spec; on an instance it is the bound
[`Signal`][flyball.foundation.device.signal.Signal] (`self.dry_flow.value`,
`.push(v)`, `.staged`, `.limits`).

## Signals: R, P, W

A **signal** is one named value of one quantity on one device. It has an
**address** — `device[.namespace…].signal`, e.g. `hum_sensors.dry.humidity`
— and an **access** set, which a signal's role sets by default and a rig
file may only narrow:

| flag | meaning | where it matters |
| --- | --- | --- |
| **R** readable | a `GET` returns a current value on demand (last known, or a fresh hardware read with `fresh=true`) | detail pages, "read now" |
| **P** published | the device emits it on its own schedule (poll or push): samples, `/ws/samples`, the store, the recorder | readouts, charts, dashboards, history |
| **W** writable | accepts a demand; a controller may target it; has `limits` (a demand is refused while a limit that follows another signal has no value yet, or a non-finite one; what an end follows -- a signal of the device, or one of its inputs by name -- is resolved once, when the device is added, and a name that matches neither refuses the device); keeps its last *set* value beside its read value | target entry, controllers, program steps |

`P` implies `R`. Typical: a thermocouple is `RP` (`Role.READOUT`); a heater's
demand is `RPW` (`Role.DEMAND`) — the readback is the committed value, so
the target and what it settled to share one address; a setting such as a
blender's `blend` is `RP` (`Role.SETTING`, read on demand, changed only by
its command, never streamed as a demand would be).

The **driver declares** each signal's access; a rig file may only
*restrict* it (`published: false` on a noisy diagnostic), never add a flag
the driver did not declare. Write it as `furnace.zone1 [RP]` in prose and
in `rig check`'s output.

## Samples are one instant

A **reading** is one value on one signal at one instant. A **sample** is
every signal under one node at **one instant**, keyed by the bound signal
objects — made once when the device is built, so nothing on the hot path
looks a name up. A sample is never mutated to add a field; a later reading
is a later sample. Only signals that publish stream and are recorded:

```
{hum_sensors.dry: {humidity: 4.1, temperature: 21.9}} @ t
```

Reading an address resolves to one of three shapes:

| `rig.resolve(address)` gives | `rig.read(...)` returns |
| --- | --- |
| a signal | a `Reading` |
| a namespace the driver reads in one transaction | a `Sample` |
| a device, or a namespace read over several transactions | `Iterator[Sample]` |

Addresses are parsed **once**, at `rig.resolve`; everything below that
carries the bound objects — a `Reading.signal`, a `Sample.node` are
references, not strings.

## No value

A reading's value may be absent: a
[`NoValue`][flyball.foundation.device.novalue.NoValue], carrying its
**quality** (`invalid`, `not_applicable`, or the rig's `stale`) and a
reason. It is never `None` (which a push reads as "nothing to push") and
never a number standing in for one; `reading.usable` says which it is,
`reading.quality` and `reading.reason` why. Nothing downstream substitutes
a value: `signal.value` (and an input's `value`, and `router.value`)
raises `NoValueError` -- a `NotReadyError`; an input has no default --
a limit that follows it is unknown (a demand is refused, a controller
held), a controller regulating on it freezes, a settle wait is not met,
and the chart breaks. `router.last_usable[signal]` keeps the newest
reading that had a value.

**The driver contract.** What a driver yields for a value it has not got
decides what the rig does:

| the driver | means | the rig |
| --- | --- | --- |
| leaves a signal out of a sample, or pushes `None` | not read this time | nothing: the last value stands |
| yields `invalid("reason", side=None)` | read, but not a valid measurement: a sensor's "no measurement", a NAMUR fault current (with its `side`), a CRC failure on one value of several | a reading of quality `invalid`: a fault |
| yields `not_applicable("reason")` | the quantity is undefined now (a blend's humidity with no flow) | quality `not_applicable`: benign, never an alarm |
| yields `None`, NaN or an infinity inside a sample | a value it could not produce | `invalid("no value")` / `invalid("not finite")` |
| yields `railed(value, "high")` | a usable value at the end of what the sensor can report | the value, with the caveat `at_limit: high` |
| raises `HardwareError` | the transport failed: nothing was read | counts toward `reads.fail_after`; at it, the device is `offline` |

A no-value is a good read of an absent value: it never counts toward the
failure budget, and one bad channel does not take the device's other
channels offline with it. A value a controller must not act on (a
warm-up placeholder) is left out -- the signal stays `pending` -- or is
`invalid`, never passed on as a number. `stale` is the rig's alone.

**Readback.** A demand declares where its reading comes from:
`readback=Readback.ECHO` (the default) when the rig pushes back the value
it committed, `Readback.SENSED` when the driver reads it back from the
hardware. While a device's writes fail (`write_failed`) its echo demands
read `stale`, reason `write_failed`, and the failed values are kept and
retried; when its reads fail (`offline`) or hang (`hung`) what its polled
reads delivered -- readouts and sensed demands -- reads `stale`, reason
`device_offline` or `device_hung`. A published readout or sensed demand
that stops arriving goes `stale` at its `stale_after_s` (default
`max(3·poll_s, 5 s)` while polled), pushed by the rig: a driver never
pushes `stale` itself. Each reading the rig delivers carries
`received_ns`, when the rig took it on its own clock, beside `time_ns`, when
it was read.

## Demands are a sample in reverse

A **demand** puts one or more values on `W` signals under one node, at one
instant, applied atomically: `rig.write(node, {name: value, ...})`. Writes
are two-phase: `apply` records a value (no I/O), and `commit` pushes
everything recorded to hardware once, at the end of a delivery — `commit`
returns nothing; a driver whose actual readback differs from the demand (a
clamp, a quantised duty) pushes it itself (`signal.push(value)`). There is
no dirty flag for a driver to maintain — the rig tracks which devices a
delivery touched.

### What a stop writes

Every device has a **resolved stop**. A driver whose command is marked
`@command(stops=True)` is stopped by that command (`Device.stops_by()`
names it, and a driver whose stop depends on its config overrides it).
Otherwise each writable demand takes the rig file's `stop:` value, else
the `off` its `SignalSpec` declares (`SignalSpec(off=...)`: the inactive
level, a logical value before `invert`, written even outside `limits`),
else `keep`: `Signal.stop` holds the rig file's value, `KEEP` (`"keep"`)
meaning leave it. A driver declares `off` only where it cannot be wrong.
A stop's values go through the rig's force path, past latches, holds,
permissives and `max_rate`. Declaring either:
[Writing an actuator](device/actuator.md#what-a-stop-does-to-it); what the
rig file says: [`stop:`](../2-config/devices/index.md#stop-what-a-stop-writes).

## Controller

A **controller** regulates one published signal, its **measured** signal,
by writing one demand, its **output**, through a law (and an optional
feedforward). It is named by the output's address, since a demand has at
most one controller:

```
heaters.heater1: { measured: furnace.zone1, law: { type: pi, kp: 100, ki: 0.15, tt: 30 } }
```

Reader/actuator and channel/loop have merged into device/signal and
signal/controller: a device page shows its signals, their controllers and
its commands together, and a controller's config is a `measured` address,
keyed by its output's address, nothing more. [How a controller works](../2-config/controllers.md#how-a-controller-works) covers the tick
in detail.

## Overlays: real vs simulated

A rig is an ordered list of files, later overlaying earlier: mappings
deep-merge, scalars and lists replace whole, `null` deletes. An overlay
swaps the **drivers** behind the **same names**, so every address,
controller, dashboard, program and recorded session is identical whether
the rig is real or simulated:

```
flyball-runner furnace.yaml            # hardware
flyball-runner furnace.yaml sim.yaml   # same addresses, no hardware
```

`examples/humidity/rig-multi-sensor.yaml` + `sim.yaml` ([the humidity rig](https://bengineer42.github.io/humctrl/), its own
book) is a real worked example: the
same `hum_sensors`/`blender` device names and signal addresses, one file
built on `sht4x_set`/`dual_pump_blender` against real I²C and PWM links,
the other on the generic `sim_daq`/`sim_drive` against one shared plant
link.

## Conditions

A **condition** is something true *now*: a device offline, a read too
slow, a sensor failed, a controller held. It is not a signal. It lives in
the rig's condition store, `rig.conditions`, keyed by the object it is
true of -- a `Device`, a `Signal`, a `Controller`, or the rig itself --
and its `code`, and it carries a `severity` (`debug`, `info`, `warning`,
`error`), a message, `since_ns`, and its owner's `scope` and `subject`.
Keyed by the object, not a name: a device removed and another added under
the same name start clean, and removing a device (or detaching a
controller) clears what it held.

Its start and end are events, told apart by `edge`: `raised` when it
begins and `cleared` when it ends, with `details.duration_s`. Only a
genuine transition makes one. Setting a condition that already holds
updates its message and nothing else, so a producer may set it on every
poll or every step; one that could flicker adds hysteresis before it sets
or clears (the runtime's `slow` does: three slow reads in a row to raise,
five fast ones to clear; a band's condition clears only after
`max(2·poll_s, 1 s)` back inside).

A driver raises its own through the device:

```python
from flyball.foundation.device import Severity

self.set_condition("railed", Severity.WARNING, "at the power limit")   # on the device
self.set_condition("broken", Severity.ERROR, "open circuit", signal=self.signals["t1"])
self.clear_condition("railed")                                        # when it no longer holds
```

`set_condition` returns whether it raised the condition (it was not held
before). The code is any stable string the driver chooses; the runtime's
own are `Code` members (`offline`, `hung`, `slow`, `write_failed`,
`stale_input`, `limit_unknown`, `frozen`, `step_failed`, `recording_failed`,
`stopped`, `latched`, `not_permitted`, and
`band_warning`/`band_alarm` on a signal whose reading is outside its
`warning`/`alarm` band, `band_unknown` on one with no value --
[Bands](../2-config/devices/index.md#bands)). Both
calls may be made from any thread, `read` and `commit` included. Before the
device is on a rig they go to a store of its own; adding it to a rig
raises what it holds there, on the rig's clock. `device.held_conditions()`
is what is held on the device and its signals now.

In-process code that must react to a condition -- a rule, a local alarm --
subscribes to the edges:

```python
unsubscribe = rig.conditions.subscribe(lambda edge: print(edge.event.edge, edge.condition.code))
```

Each `ConditionEdge` carries the `owner` object, the `condition` and the
recorded `event`. Subscribers are called in order on the store's own
thread, never on the producer's, so never under the rig's lock: a
subscriber may do I/O, and one that raises is logged without stopping the
others.

## What is declared, what runs, what happened

Three kinds of object, told apart by who changes them:

| kind | objects | mutable? | changed by |
| --- | --- | --- | --- |
| **what a driver declares** | a driver's `SignalSpec`/`NodeSpec`, a `Quantity` | frozen | never; the driver's word |
| **the running rig** | `Node`, `Signal`, `Device`, `Rig`, `Controller` | mutable, identity-hashed, made once at startup | the rig, under its lock, as an event — the rig file's signal metadata, a live limit change, a controller attached |
| **one instant** | `Reading`, `Sample`, `Write`, `WriteState`, `Event`, `Condition` | frozen | never after the fact; recorded, streamed, compared (a condition held again with a new message is a new `Condition` in its place) |

A device's own signals follow the same split: a role's *access* is
declared; what it is built from (class defaults, config and the rig file,
and metadata an instance sets in `__init__`, such as a limit from a
measured maximum) is set when the rig is built; a
`Role.DEMAND`/`Role.SETTING`/`Role.READOUT` signal's readings are facts
about one instant.
`Signal.spec` is what the driver declared; `Signal.access` and its
metadata as the rig file set it are what is in force — `rig check`, the
wire and the UI can show both ("driver says RPW, file made it RP").

Next: [How a controller works](../2-config/controllers.md#how-a-controller-works).
