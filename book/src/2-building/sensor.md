# Writing a sensor

A sensor is three declarations and one class.

## Declare what is measured

```python
--8<-- "sensor.py:11:15"
```

A measurand is interned on its name: the second module to say
`Measurand("temperature", Celsius)` gets the same object, and one that says
`Measurand("temperature", Kelvin)` gets `MeasurandConflictError`. Declare
them as module constants and import them.

`range` and `precision` are for gauges and axes. They land in the schema, so
a UI or a CLI knows how to draw the channel without being told.

A source declares every measurand it will ever report, at construction. A
sensor that measures two things in one transaction is one source with two
measurands; two sensors are two sources.

## Choose a unit

Units come from `flyball.core.units`: the SI base and derived units, °C, °F,
litres, minutes, and prefixes (`Pascal.prefixed(Kilo)`, both from `flyball.core.units.si` and `.dimension`). Anything not there
is one line:

```python
from flyball.core.units import DIMENSIONLESS
PercentRH = DIMENSIONLESS.unit("percent relative humidity", "%RH", 0.01)
```

The framework never converts. A reading is a bare float in the measurand's
unit, and the driver is responsible for reporting in exactly that unit. If
the chip speaks Kelvin and the measurand says °C, subtract 273.15 in `read`.

## A polled reader

The rig calls `read(time_ns)` on the reader's period and delivers what comes
back:

```python
--8<-- "sensor.py:18:29"
```

`time_ns` is the rig's clock at the moment of the poll; stamp every sample
with it unless the hardware gives a better timestamp. `seq` comes from
`source.next_seq()` so every sample of a source is one numbered series.

## A pushed reader

Some hardware delivers on its own schedule — a serial stream, a callback, a
subscription. Then nothing polls; the reader hands samples on as they arrive:

```python
--8<-- "sensor.py:32:40"
```

`push` is safe from any thread. Before the reader is attached to a rig, pushed
samples are held and delivered when it is.

A reader may do both. The one contract: samples reach the rig in
non-decreasing `time_ns`. Equal stamps are allowed — a bank read in one
transaction shares one — a step backwards is not. The rig never reorders, so
every observer downstream inherits that guarantee.

## Talking to an instrument

A reader that speaks to a bench instrument takes a **link** rather than
opening a port itself: `TextLink` (`write`, `query`) for SCPI and
line-oriented serial, `RegisterLink` (`read_registers`, `write_registers`)
for Modbus. Each link has a real implementation that imports its driver only
when built, and a fake for tests and hardware-free rigs. A device that takes
the protocol can be given either. `flyball.devices` has table-driven
readers and actuators over both; subclass those before writing a driver
from scratch.

## Several devices, one instant

Many sensors split a measurement into *trigger* (start converting) and
*collect* (wait, then read). `flyball.hardware.bank.Bank` triggers every
device before collecting any, so three sensors cost one conversion time and
their samples are stamped within microseconds. Give it devices satisfying
`TwoPhase` (`trigger()` and `collect()`); it maps each key to its result or
the exception that stopped it.

## Config, settings, state, commands

A reader is a device, so it may declare the three tiers and mark commands the
same way an actuator does — see [Writing an actuator](actuator.md). A reader
with nothing to configure or set declares nothing and still answers `view`.

## What the runtime adds

The rig keeps a run record beside each reader: its period, when it last
delivered, and an `offline` condition if a read raised. Polling continues
after a failure, and the next successful delivery clears the condition;
nothing else in the rig stops. `GET /api/readers/{name}` shows both the
device's own state and the run.
