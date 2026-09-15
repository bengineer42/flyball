# A quantity, an actuator, a source

Flyball's vocabulary is small. Seven nouns cover everything that is measured
and everything that acts, and the rest of the library is arithmetic over them.

## Measurand

What is measured: a name and a unit, plus a plausible range and a display
precision for a gauge or an axis.

```python
from flyball.core import Measurand
from flyball.core.units.si import Celsius

TEMPERATURE = Measurand("temperature", Celsius, range=(0.0, 300.0), precision=1)
```

A measurand is **interned on its name**. Declaring `"temperature"` twice gives
the same object; declaring it again with a different unit is an error. So a
measurand is a process-wide constant, and the application declares them — the
library names none.

Units belong here and nowhere else. A reading of `temperature` is in °C
because the measurand says so; a driver that reads Kelvin converts before it
reports.

## Source

One thing that emits readings: a sensor, a fused estimate, a processor's
output. It declares its channels once, at construction, and registers itself
by name.

```python
from flyball.core import Source

probe = Source("probe", (TEMPERATURE,))
```

Names are unique per process. Two sources with the same name is an error, not
a merge.

## Channel

One measurand from one source: `probe[TEMPERATURE]`, or on the wire
`{"source": "probe", "measurand": "temperature"}`. Only the source constructs
one, so there is exactly one object per pair and equality is identity. A
channel is what a loop regulates and what an observer subscribes to.

## Reading and Sample

A **reading** is one value on one channel at one instant. A **sample** is
every measurand of one source at one instant — what a sensor that measures
temperature and pressure in one transaction produces. A sample flattens into
readings; the rig routes readings, never samples.

Time is an integer count of nanoseconds. Nothing in flyball stores a timestamp
as a float, because a difference of two wall-clock floats carries ~240 ns of
error regardless of how small the interval is.

## Device

Anything the rig is made of: a reader or an actuator. A device describes
itself in three tiers, told apart by *who changes them*:

| tier | changed by | example |
| --- | --- | --- |
| **config** | the builder, by rebuilding | I²C address, pump pair, name |
| **settings** | an operator or a program, by a command | a blend policy, a power limit |
| **state** | the process, every tick or read | demand, last reading, conditions |

A **condition** is something true of a device *now* — offline, railed,
overdriven. It lives in state while it holds and goes when it clears, so a
client joining late sees the present, not a log.

An **event** is something that *happened* — a step failed, a reader went
offline, a wait timed out. It is a point in time with a level, a scope and a
subject, kept in a short recent list, streamed on `/ws/events`, and written
to the session if one is recording. A condition says what is wrong; the
event says when it went wrong.

Methods marked `@command` are the actions a device offers. They become HTTP
routes, CLI subcommands and program steps without further code.

## Reader

A device that delivers samples for one or more sources. Either the rig polls
it on a period (`read`), or something else pushes samples into it (`push`), or
both. The layering is `Source → Reader → Rig`; nothing sits between.

## Actuator

A device a loop drives. It has exactly one method beyond a device's:

```python
def set_demand(self, demand: float) -> float | None: ...
```

*Given a demand in the controlled quantity's units, do what you can and report
what you expect to deliver, in the same units.* `None` means "I cannot say".
That single method is the whole boundary between the control library and the
hardware.

## How they fit

```
Measurand ──declared by──▶ Source ──has──▶ Channel
                                              │
   Reader ──delivers Samples──▶ Rig ──routes Readings on──┘
                                 │
                                 ▼
                          Loop(channel, law, actuator) ──set_demand──▶ Actuator
```

Next: [The loop](loop.md).
