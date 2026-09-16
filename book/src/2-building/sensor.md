# Writing a sensor

A sensor is a `Readable` device: it implements `read`, and its signals are
`R`/`P` (`Output`, the default role). There is no separate `Reader` class —
a sensor, an actuator with a readback, and a multi-channel instrument are
all `Device`, told apart by which of `Readable`/`Committable` they are and
which access flags their signals carry.

## Declare what is measured

```python
--8<-- "sensor.py:11:13"
```

A [`Quantity`][flyball.core.quantity.Quantity] is a name and a unit, nothing
else — not interned, not process-wide. Two devices may both report
`temperature` in °C without sharing an object; range, precision and bands
live on the *signal*, not the quantity, because two thermocouples on one
rig can differ in all three.

## Declare the tree

```python
--8<-- "sensor.py:15:32"
```

`TREE` is a tuple of [`SignalSpec`][flyball.core.signal.SignalSpec] (a leaf)
and [`NodeSpec`][flyball.core.signal.NodeSpec] (a namespace grouping
several). `Device.__init__` binds it once: every signal becomes a bound
[`Signal`][flyball.core.signal.Signal] object with its address
(`weather.temperature`) fixed for the device's life. `range` and
`precision` land in the schema, so a UI or a CLI knows how to draw the
signal without being told.

## Choose a unit

Units come from `flyball.core.units`: the SI base and derived units, °C,
°F, litres, minutes, and prefixes (`Pascal.prefixed(Kilo)`, both from
`flyball.core.units.si` and `.dimension`). Anything not there is one line:

```python
from flyball.core.units import DIMENSIONLESS
PercentRH = DIMENSIONLESS.unit("percent relative humidity", "%RH", 0.01)
```

The framework never converts. A reading is a bare float in the signal's
unit, and the driver is responsible for reporting in exactly that unit. If
the chip speaks Kelvin and the signal says °C, subtract 273.15 in `read`.

## A polled device

The rig calls `read(time_ns, node)` on the device's period and delivers
what comes back — `read` yields one `Sample` per instant actually read,
keyed by the bound signal objects, never by name:

```python
--8<-- "sensor.py:35:48"
```

`time_ns` is the rig's clock at the moment of the poll; stamp the sample
with it unless the hardware gives a better timestamp. A device is polled on
the smallest `poll_s` over its publishing signals — set it on the device
(`weather.poll_s = 1.0`) or per-signal for a mixed rate; `None` (the
default) means never polled, the shape [Assembling a rig](rig.md#devices)
covers.

## A pushed device

Some hardware delivers on its own schedule — a serial stream, a callback, a
subscription. Then nothing polls; the device calls `rig.on_samples` itself
as data arrives, from whatever thread that is:

```python
--8<-- "sensor.py:51:68"
```

`on_samples` takes the rig's own lock, so it is safe from any thread. A
device may do both — accept a poll and also push between polls — the one
rule is that samples reach the rig in non-decreasing `time_ns` per device;
the rig never reorders, so every observer and controller downstream
inherits that guarantee.

## Talking to an instrument

A device that speaks to a bench instrument takes a **link** rather than
opening a port itself: `TextLink` (`write`, `query`) for SCPI and
line-oriented serial, `RegisterLink` (`read_registers`, `write_registers`)
for Modbus. Each link has a real implementation that imports its driver
only when built, and a fake for tests and hardware-free rigs. A driver
config's `link` field names one by key in the rig file's `links:` — see
[Config and build](config.md). `flyball.hardware` and `flyball.devices`
have table-driven devices over both; subclass those before writing a
driver from scratch.

## Several signals, one instant

Many sensors split a measurement into *trigger* (start converting) and
*collect* (wait, then read). `flyball.hardware.bank.Bank` triggers every
device before collecting any, so three sensors cost one conversion time and
their samples are stamped within microseconds. Give it devices satisfying
`TwoPhase` (`trigger()` and `collect()`); it maps each key to its result or
the exception that stopped it.

## Config and commands

A sensor is a device like any other, so it may declare a `ConfigSignal` and
mark commands the same way a writable device does — see
[Writing an actuator](actuator.md#demand-output-and-setting). One with
nothing to configure declares nothing.

## What the runtime adds

The rig keeps a run record beside each polled device: its period, when it
last delivered, and an `offline` condition if a read raised. Polling
continues after a failure, and the next successful delivery clears the
condition; nothing else in the rig stops. `GET /api/devices/{name}` shows
both the device's own state and the run (`run: {period_s, running,
last_read_ns}`).
