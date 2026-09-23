# Writing a sensor

A sensor is a `Readable` device: it implements `read`, and its signals are
`R`/`P` (`Readout`, the default role). There is no separate `Reader` class —
a sensor, an actuator with a readback, and a multi-channel instrument are
all `Device`, told apart by which of `Readable`/`Committable` they are and
which access flags their signals carry.

## Declare what is measured

```python
--8<-- "sensor.py:quantities"
```

A [`Quantity`][flyball.foundation.quantities.quantity.Quantity] is a name and a unit, nothing
else — not interned, not process-wide. Two devices may both report
`temperature` in °C without sharing an object; range, precision and bands
live on the *signal*, not the quantity, because two thermocouples on one
rig can differ in all three.

## Declare the tree

```python
--8<-- "sensor.py:polled-signals"
```

Each signal is a **descriptor** on the class: [`Readout`][flyball.foundation.device.descriptors.Readout]
for something produced (`RP`), the only role a pure sensor needs. Its
arguments are the signal's name, a label, its quantity, then metadata:
`range` and `precision` for a gauge or an axis, `warning` and `alarm` bands,
`poll_s` for a signal read at its own rate, `tags` to group it across
devices. A [`Namespace`][flyball.foundation.device.descriptors.Namespace] groups several
under one path (`hum_sensors.dry.humidity`), for a device that is really
several sensors. `Device.__init__` binds the tree once: every descriptor
becomes a bound [`Signal`][flyball.foundation.device.signal.Signal] with its address
(`weather.temperature`) fixed for the device's life, reachable as
`self.temperature` or `self.signals["temperature"]`. Everything declared
here lands in the schema, so a UI or a CLI knows how to draw the signal
without being told; a rig file may narrow it (`signals:` metadata) but
never widen it.

A driver whose tree depends on its config -- a table of SCPI queries, a
register map -- builds descriptors at run time instead;
[Config and build](config.md) shows that shape.

## Choose a unit

Units come from `flyball.foundation.quantities`: the SI base and derived units, °C,
litres and minutes (`flyball.foundation.quantities.si`), °F and °R (`.other`), and
prefixes (`Pascal.prefixed(Kilo)`, `Kilo` from `.dimension`). A rig file names any of
them by symbol. Anything not there is one line:

```python
from flyball.foundation.quantities import DIMENSIONLESS
PercentRH = DIMENSIONLESS.unit("percent relative humidity", "%RH", 0.01)
```

The framework never converts. A reading is a bare float in the signal's
unit, and the driver is responsible for reporting in exactly that unit. If
the chip speaks Kelvin and the signal says °C, subtract 273.15 in `read`.

## A polled device

The rig calls `read(time_ns, node)` on the device's period and delivers
what comes back — `read` yields one `Sample` per instant actually read.
`self.sample(time_ns, **values)` builds one from descriptor names:

```python
--8<-- "sensor.py:polled-read"
```

`time_ns` is the rig's clock at the moment of the poll; stamp the sample
with it unless the hardware gives a better timestamp. A device is polled on
the smallest `poll_s` over its published signals — set it on the device
(`weather.poll_s = 1.0`) or per-signal for a mixed rate; `None` (the
default) means never polled, the shape [Assembling a rig](../rig.md#devices)
covers.

## A pushed device

Some hardware delivers on its own schedule — a serial stream, a callback, a
subscription. Then nothing polls; the device pushes as data arrives, from
whatever thread that is:

```python
--8<-- "sensor.py:pushed"
```

`self.push(time_ns, **values)` is one sample, one delivery; `signal.push(value)`
is one value, and `with self.batch():` gathers several such pushes into one
sample. All take the rig's own lock, so they are safe from any thread. A
device may do both — accept a poll and also push between polls — the one
rule is that samples reach the rig in non-decreasing `time_ns` per device;
the rig never reorders, so every observer and controller downstream
inherits that guarantee. A device with nothing to poll leaves `poll_s`
unset in the rig file; its `read` yields nothing.

## Talking to an instrument

A device that speaks to a bench instrument takes a **link** rather than
opening a port itself: `TextLink` (`write`, `query`) for SCPI and
line-oriented serial, `RegisterLink` (`read_registers`, `write_registers`)
for Modbus. Each link has a real implementation that imports its driver
only when built, and a fake for tests and hardware-free rigs. A driver
config's `link` field names one by key in the rig file's `links:` — see
[Config and build](config.md). `extensions/visa` (`Scpi`) and
`extensions/modbus` (`Modbus`) have table-driven devices over both;
subclass those before writing a driver from scratch.

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
[Writing an actuator](actuator.md#demand-readout-and-setting). One with
nothing to configure declares nothing. To be named in a rig file it needs a
config class with a type -- [Config and build](config.md) -- after which it
appears in [Supported drivers](../../2-config/devices/drivers.md)' terms: its own
fields, a `link`, the envelope around it.

## What the runtime adds

The rig keeps a run record beside each polled device: its period, when it
last delivered, and an `offline` condition if a read raised. Polling
continues after a failure, and the next successful delivery clears the
condition; nothing else in the rig stops. `GET /api/devices/{name}` shows
both the device's own state and the run (`run: {period_s, running,
last_read_ns}`).
