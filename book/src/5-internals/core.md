# Core

Values, and the machinery every layer needs. No opinions about what is
controlled.

## Time

`Clock` maps a monotonic timebase to wall clock. `now_ns()` is wall-clock
nanoseconds; `from_start_s(time_ns)` is seconds since the clock's origin,
computed by integer subtraction first so precision does not depend on the
magnitude of the timebase.

`Time`, `Duration` and `Rate` are integer-nanosecond quantities that
serialise as `{seconds, nanoseconds}` rather than floats, so a timestamp
survives a round trip exactly. `Duration` and `Rate` also accept unit-keyed
wire forms — `{minutes: 10}`, `{per_minute: 2}` — because that is how a
person writes them.

## Units

A small, closed model, not a units algebra. Seven `BaseDimension`s plus angle
and solid angle, kept apart so rad/s and Hz differ. A `Dimension` is a
product of their powers stored as a sorted tuple, so equality is a tuple
compare and `Torque == Energy` holds; a `NamedDimension` adds a display name
without touching identity.

A `Unit` is a magnitude on a dimension: a `factor` to the coherent SI unit
and, for absolute scales such as °C, a `zero`. Intervals and anything
composed ignore the zero; only a point on a scale uses it. Whether a value
is a point or an interval is the measurand's business, not the unit's.

`Quantity(unit, **constraints)` annotates a float with its unit for pydantic:
the unit lands in the JSON schema, `unit_of(cls, field)` reads it back
in-process. Values in the framework are bare floats in the declared unit;
conversion from a device's native unit happens in the driver.

## Readings

| Type | Is | Identity |
| --- | --- | --- |
| `Measurand` | what is measured: unit, label, plausible range, precision | interned on name |
| `Source` | one thing that emits readings | registered by name |
| `Channel` | one measurand from one source | one object per pair, by construction |
| `Reading` | one value on one channel at one instant | a plain frozen value |
| `Sample` | every measurand of one source at one instant | the same |

A source declares its channels once, at construction, and nothing else
constructs a `Channel`, so routing a reading is a pointer lookup. On the wire
a channel is `{source, measurand}` by name; decoding looks them up in their
registries, so a trace that names a source this process does not have fails
validation rather than inventing one.

Registries are process-wide today. That is the main thing standing between
the library and running two rigs in one process; see
[Architecture](architecture.md#where-it-is-going).

## Devices

Three tiers — config, settings, state — told apart by who changes them, and
`@command` methods. `Device.__init_subclass__` reads the property
annotations, checks each derives from the right base and that pydantic can
describe it, and collects commands, checking every argument and return can
cross the wire. A mistake fails at import, not on the first request.

`Reader` adds the delivery path (`emit`, `push`, `read`); `Actuator` adds
`set_demand` and is also a `Sink`, something that commits on `apply`.

## Errors

Six bases, classified by what a caller can do about a failure rather than
which subsystem raised it: `NotFoundError`, `ConflictError`, `NotReadyError`,
`UnachievableError`, `HardwareError`, and `FlyballError` as the root. Each
mixes in the builtin a consumer would reach for, so `except LookupError`
behaves as expected. The HTTP layer maps the six once; no other code decides
status codes.

## Topic and Latest

`Topic` is thread-to-asyncio fan-out with drop-oldest, for streams: samples.
Publishing with no subscriber is a no-op, so an idle server costs the control
thread nothing.

`Latest` keeps only the newest value per key. The writer does one dict store
per update; readers poll at their own rate and ask for what changed since the
version they last saw. A loop at any tick rate costs the same, and a socket
sends at most one frame per flush. Loop states, actuator states, reader runs
and signal outcomes all go through `Latest`; only samples are a `Topic`.
Both are filled only while someone is watching.

## Signal

The wait primitive: fires once, says how it ended (fired, timed out,
interrupted), and calls `on_settle` so a registry can publish the outcome
from whichever thread settled it.

## Config

`Config[T]` with `build() -> T`; `ConfigOr[T]` and `resolve` so any component
can be given either a built object or a description of one. Configs hold
real defaults rather than `None` sentinels, so a serialised config records
what the rig actually did.

## Resources

`Resource` and `Operator` form a claim graph answering "who may drive this
right now" — distinct from *mode*, which answers "what is it doing". Whether
the graph survives is open; nothing in the current milestone uses it.
