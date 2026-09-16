# The device model

Flyball's vocabulary is small. A handful of nouns cover everything the rig
is made of, and the rest of the library is arithmetic over them.

## Device

Everything with a name is a **device**: a sensor, a relay, a multi-channel
instrument, a composite like a split-range blender. A device has a tree of
**signals** (namespaces group them into sub-devices), commands, and state.
There is no separate reader or actuator class — what a device *is* falls
out of which access flags its signals carry.

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

## Signals: R, P, W

A **signal** is one named value of one quantity on one device. It has an
**address** — `device[.namespace…].signal`, e.g. `hum_sensors.dry.humidity`
— and an **access** set:

| flag | meaning | where it matters |
| --- | --- | --- |
| **R** readable | a `GET` returns a current value on demand (last known, or a fresh hardware read with `fresh=true`) | detail pages, "read now" |
| **P** publishing | the device emits it on its own schedule (poll or push): samples, `/ws/samples`, the store, the recorder | readouts, charts, dashboards, history |
| **W** writable | accepts a demand; a controller may target it; has `limits`, `together`; keeps its last *set* value beside its read value | target entry, controllers, program steps |

`P` implies `R`. Typical: a thermocouple is `RP`; a heater relay `W`; a
setting such as a blender's `blend_flow` is `RW` (read on demand, not
streamed); a single-register PSU voltage genuinely is `RPW`. The
**convention** is to keep set and measured values on separate addresses
(`psu.set_voltage [W]`, `psu.output_voltage [RP]`); `RPW` is reserved for
hardware that really is one register.

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
instant, applied atomically: `rig.demand(node, {name: value, ...})`.
Signals a driver declares `together` (a blender's `dry_flow`/`wet_flow`)
must arrive in the same demand — a lone write to one is refused. Writes are
two-phase: `apply` records a value (no I/O), and `commit` pushes everything
recorded to hardware once, at the end of a delivery. There is no dirty flag
for a driver to maintain — the rig tracks which devices a delivery touched.

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
a `target` address, nothing more. [The controller](loop.md) covers the tick
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

`examples/humidity/rig.yaml` + `sim.yaml` is a real worked example: the
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

This is a device's own config/settings/state split applied to the whole
graph: the structure tier is the rig's settings layer. `Signal.spec` is
what the driver declared; `Signal.access` and its overridden metadata are
what is in force — `rig check`, the wire and the UI can show both ("driver
says RPW, file made it RP").

Next: [The controller](loop.md).
