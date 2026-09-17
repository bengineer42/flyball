# The device model

Flyball's vocabulary is small. A handful of nouns cover everything the rig
is made of, and the rest of the library is arithmetic over them.

## Device

Everything with a name is a **device**: a sensor, a relay, a multi-channel
instrument, a composite like a split-range blender. A device has a tree of
**signals** (namespaces group them into sub-devices), commands, and
**conditions** — what the driver says is true of it now (railed, offline,
overdriven), pushed onto the base class's own `conditions` output. There is
no separate reader or actuator class: a device is `Readable` (implements
`read`, produces samples on a schedule) if it polls, `Committable`
(implements `apply`/`commit`) if it has demands, both, or neither — what
falls out of that is which access flags its signals carry.

```python
from flyball.core import Quantity
from flyball.core.units.si import Celsius

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
| `Role.DEMAND` | `RPW` | settable, with a current value (its readback) that updates — what a controller drives |
| `Role.OUTPUT` | `RP` | produced, never set: a measurement, a derived value, a mode |
| `Role.SETTING` | `RP` | re-set by a command while the device runs, shown; not driven by a controller |
| `Role.CONFIG` | `R` | effective at build, shown, never set at run time |
| `Role.INPUT` | — | another device's signal, bound by the rig to a role; not in the tree |

Structure is declared once, as descriptors in the class body (`Namespace`,
`Demand`, `Output`, `Setting`, `ConfigSignal`, `Input`), or built from config
in `__init__` with the same factories and bound with `Device.bind`.
`Section("dry", "Dry line")` in place of a name tags a second grouping axis
across the tree, orthogonal to the namespace. On the class a descriptor is
its spec; on an instance it is the bound
[`Signal`][flyball.core.signal.Signal] (`self.dry_flow.value`,
`.push(v)`, `.pending`, `.limits`).

## Signals: R, P, W

A **signal** is one named value of one quantity on one device. It has an
**address** — `device[.namespace…].signal`, e.g. `hum_sensors.dry.humidity`
— and an **access** set, which a signal's role sets by default and a rig
file may only narrow:

| flag | meaning | where it matters |
| --- | --- | --- |
| **R** readable | a `GET` returns a current value on demand (last known, or a fresh hardware read with `fresh=true`) | detail pages, "read now" |
| **P** publishing | the device emits it on its own schedule (poll or push): samples, `/ws/samples`, the store, the recorder | readouts, charts, dashboards, history |
| **W** writable | accepts a demand; a controller may target it; has `limits`; keeps its last *set* value beside its read value | target entry, controllers, program steps |

`P` implies `R`. Typical: a thermocouple is `RP` (`Role.OUTPUT`); a heater's
demand is `RPW` (`Role.DEMAND`) — the readback is the committed value, so
the target and what it settled to share one address; a setting such as a
blender's `blend` is `RP` (`Role.SETTING`, read on demand, changed only by
its command, never streamed as a demand would be).

The **driver declares** each signal's access; a rig file may only
*restrict* it (`publishing: false` on a noisy diagnostic), never add a flag
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

## Demands are a sample in reverse

A **demand** puts one or more values on `W` signals under one node, at one
instant, applied atomically: `rig.demand(node, {name: value, ...})`. Writes
are two-phase: `apply` records a value (no I/O), and `commit` pushes
everything recorded to hardware once, at the end of a delivery — `commit`
returns nothing; a driver whose actual readback differs from the demand (a
clamp, a quantised duty) pushes it itself (`signal.push(value)`). There is
no dirty flag for a driver to maintain — the rig tracks which devices a
delivery touched.

## Controller

A **controller** binds one publishing signal to one writable signal
through a law (and an optional feedforward). It is named by the address of
the signal it drives, since a writable signal has at most one controller:

```
heaters.heater1: { signal: furnace.zone1, law: { tag: PI, kp: 100, ki: 0.15, tt: 30 } }
```

Reader/actuator and channel/loop have merged into device/signal and
signal/controller: a device page shows its signals, their controllers and
its commands together, and a controller's config is a `source` address and
a `target` address, nothing more. [How a controller works](../2-config/controllers.md#how-a-controller-works) covers the tick
in detail.

## Overlays: real vs simulated

A rig is an ordered list of files, later overlaying earlier: mappings
deep-merge, scalars and lists replace whole, `null` deletes. An overlay
swaps the **drivers** behind the **same names**, so every address,
controller, dashboard, program and recorded session is identical whether
the rig is real or simulated:

```
flyball-daemon furnace.yaml            # hardware
flyball-daemon furnace.yaml sim.yaml   # same addresses, no hardware
```

`examples/humidity/rig.yaml` + `sim.yaml` ([the humidity rig](https://bengineer42.github.io/flyball/humidity/), its own
book) is a real worked example: the
same `hum_sensors`/`blender` device names and signal addresses, one file
built on `sht4x_set`/`dual_pump_blender` against real I²C and PWM links,
the other on the generic `sim_daq`/`sim_drive` against one shared plant
link.

## Three tiers, told apart by who changes them

| tier | objects | mutable? | changed by |
| --- | --- | --- | --- |
| **declaration** | a driver's `SignalSpec`/`NodeSpec`, a `Quantity` | frozen | never; the driver's word |
| **structure** (rig level) | `Node`, `Signal`, `Device`, `Rig`, `Controller` | mutable, identity-hashed, made once at startup | the rig, under its lock, as an event — a file override, a live limit change, a controller attached |
| **values** (per instant) | `Reading`, `Sample`, `Demand`, `WriteState`, `Event` | frozen | never after the fact; recorded, streamed, compared |

A device's own signals echo the same split at finer grain: a role's *access*
is the declaration tier, a `Role.CONFIG` signal is effective at the
structure tier (merged from class defaults, config and the rig file at
build), and a `Role.DEMAND`/`Role.SETTING`/`Role.OUTPUT` signal's readings
are the values tier. `Signal.spec` is what the driver declared;
`Signal.access` and its overridden metadata are what is in force —
`rig check`, the wire and the UI can show both ("driver says RPW, file made
it RP").

Next: [How a controller works](../2-config/controllers.md#how-a-controller-works).
