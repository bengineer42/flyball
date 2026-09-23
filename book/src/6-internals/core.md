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
is a point or an interval is the signal's business, not the unit's.

`Measured(unit, **constraints)` annotates a float with its unit for pydantic:
the unit lands in the JSON schema, `unit_of(cls, field)` reads it back
in-process. Values in the framework are bare floats in the declared unit;
conversion from a device's native unit happens in the driver.

## Quantity, signal, address

A [Quantity][flyball.foundation.quantities.quantity.Quantity] is what is measured or set,
independent of any device: a name and a unit, nothing else. It is **not
interned** — two devices that both report `temperature` in °C hold equal
quantities (`Quantity("temperature", "°C") == Quantity("temperature", "°C")`)
without sharing an object. Range, precision, bands and limits live on the
*signal*, not the quantity, so two thermocouples on one rig can differ in
all of them. A unit derives its own: `Celsius.quantity()` is
`Quantity("temperature", Celsius)`, the name being the dimension's,
lower-cased; a composed unit on an unnamed dimension has no default and
`Unit.quantity()` raises `ValueError` without a name. Dissolving measurand interning this way is one of the things
the device model fixes over the model it replaced — see
[Decisions](decisions.md).

A driver declares its tree once, as frozen
[SignalSpec][flyball.foundation.device.signal.SignalSpec] leaves grouped by
[NodeSpec][flyball.foundation.device.signal.NodeSpec] namespaces. Each leaf carries an
[Access][flyball.foundation.device.signal.Access] set — **R**eadable (a `GET` returns a
value on demand), **P**ublishing (emitted on the device's own schedule;
implies R), **W**ritable (accepts a demand) — and `Access.check` refuses `P`
without `R` the moment a set is built, an `Access.parse`d, or a
`SignalSpec` constructed, not later at first use. The rig file may only
*narrow* a signal's access (`SignalMeta.readable`/`published`/`writable`
take only `false`) — never add what the driver did not declare.

`Device.bind` turns a spec tree into bound [Node][flyball.foundation.device.signal.Node]
and [Signal][flyball.foundation.device.signal.Signal] objects once, at construction:
identity-hashed, made once, so a `Reading`, `Sample`, `Write` or
`WriteState` holds a reference and nothing on the hot path looks a name up.
[Path][flyball.foundation.device.signal.Path] is the address's value type — a tuple of
segments, hashable, `str()` giving the dotted form (`"dry.humidity"`) —
owned by the bound object it belongs to. Strings exist only at the wire and
in the rig file; [Rig.resolve][flyball.rig.rig.Rig.resolve] is the one
place an address is parsed, below which everything carries the bound
objects.

| type | is |
| --- | --- |
| `Reading` | one value on one signal at one instant |
| `Sample` | the readings of signals under one node at one instant, keyed by the bound signal; any readable signal under the node may be missing, but there is always at least one |
| `Write` | one or more values written to `W` signals under one node at one instant — a Sample in reverse |
| `WriteState` | what a device reports after a commit: the value after limits, what was asked for if the clamp changed it, `at_limit`, the controller driving it |

## Mutability: three tiers

Three kinds of object, told apart by who changes them and when:

| tier | objects | mutable? |
| --- | --- | --- |
| declaration | `SignalSpec`, `NodeSpec`, `Quantity` | frozen — the driver's word; one spec object may back many devices |
| structure (rig level) | `Node`, `Signal`, `Device`, `Rig`, `Controller` | mutable, identity-hashed, made once at startup — the running graph: things happen *to* them |
| values (per instant) | `Reading`, `Sample`, `Write`, `WriteState`, `Event` | frozen — facts about one moment; recorded, streamed, compared; never changed after the fact |

A device's own signal roles echo the same split at finer grain (below): a
`Role.CONFIG` signal is effective at the structure tier; a
`Role.DEMAND`/`Role.SETTING`/`Role.READOUT` signal's readings are the values
tier. Two rules
keep "mutable" from meaning "anything goes": after startup, mutation goes
through the rig, under its lock, and is an event — `Signal.set_meta(...)`
and `restrict(...)` are the primitives, but the only caller once the rig
runs is a `Rig` method that takes the lock, applies the change,
re-validates what depends on it, and emits an event; and declared and
effective both stay visible — `Signal.spec` is what the driver declared,
`Signal.access` is what is in force, so `rig check` and the wire can show
both.

## Devices

Every signal has a **role** (`Role.DEMAND`, `Role.READOUT`, `Role.SETTING`,
`Role.CONFIG`), which sets its default access; an input is not a signal of
the device and has no role. Structure is declared once as descriptors in
the class body (`Namespace`, `Demand`, `Readout`, `Setting`, `ConfigSignal`,
`Input`) or built from config in
`__init__` and bound with `Device.bind`. `Device.__init_subclass__` collects
every descriptor into `DESCRIPTORS`, checks `vtype` and `config`'s return
type against pydantic, and collects `@command` methods — checking every
argument and return can cross the wire, and synthesising a `set_<path>` for
every demand no command links to. A mistake fails at import, not on the
first request.

A device with only `R`/`P` signals is a plain sensor; one with a `Demand`
drives something; one with both is both at once. There is no separate
reader/actuator class any more:
[Readable][flyball.foundation.device.device.Readable] implements
[read][flyball.foundation.device.device.Readable.read] for the polled side and
[Committable][flyball.foundation.device.device.Committable] implements
[apply][flyball.foundation.device.device.Committable.apply] /
[commit][flyball.foundation.device.device.Committable.commit] for the demand side;
`cls.readable`/`cls.writable` are derived from whether `read`/`commit` is
defined. A device may be either, both, or neither, and separately declare
`Input` signals — another device's signal the rig binds to a role; when one
lands the rig commits the device, which reads it itself
(`self.<input>.value`, from the router) inside `commit`. There is no
`observe` callback.

Writes are two-phase: `apply(signal, time_ns, value)` records one value
with no hardware I/O, and `commit(time_ns) -> None` pushes everything
recorded to the hardware once. `commit` returns nothing: the rig reports
each staged demand as the readback the driver pushed
(`signal.push(value, time_ns)`), or the committed value if the driver
pushed nothing itself; a demand that railed has its `signal.at_limit` set
before `commit` returns. The rig calls `commit` once per delivery for every
device it touched, and immediately after a manual demand; the rig tracks
which devices a delivery touched, so a driver keeps no dirty flag of its
own. A simple device inherits both `apply` and the default `commit`; a
composite one (blending two pumps into one settable humidity) overrides
`commit` itself to do arithmetic across everything staged and everything
it reads from its inputs, so a new target, a changed input reading and a
new setting arriving in one delivery still cost one write.

What a commit pushes is delivered next, as one more delivery, and so on
until nothing is left. A controller steps at most once in that chain: when
its target's device reads its source back in `commit` (one instrument's
output and process value), the readback lands -- in the router, the
streams, the recorder -- without stepping the controller again, which
would otherwise commit, push and step for ever. It steps on the next
delivery that starts a chain: the next poll.

## Errors

Six bases, classified by what a caller can do about a failure rather than
which subsystem raised it: `NotFoundError`, `ConflictError`, `NotReadyError`,
`UnachievableError`, `HardwareError`, and `FlyballError` as the root. Each
mixes in the builtin a consumer would reach for, so `except LookupError`
behaves as expected. The HTTP layer maps the six once; no other code decides
status codes. A subsystem's own error inherits its subsystem's base and one of
the six, and the map finds the second through the MRO: the store's
`StoreUnavailableError(StoreError, HardwareError)` is a 503 with no entry of
its own.

## Topic and Latest

`Topic` is thread-to-asyncio fan-out with drop-oldest, for streams: samples.
Publishing with no subscriber is a no-op, so an idle server costs the control
thread nothing.

`Latest` keeps only the newest value per key. The writer does one dict store
per update; readers poll at their own rate and ask for what changed since the
version they last saw. A store and a read share a short lock, so a reader on
another thread never sees a version before the value stored under it. A controller at any tick rate costs the same, and a
socket sends at most one frame per flush. Controller states, write states,
polling runs and trigger outcomes all go through `Latest`; only samples are
a `Topic`. Both are filled only while someone is watching.

## Trigger

The wait primitive (`flyball.foundation.router.trigger.Trigger`, not to be confused with
`flyball.foundation.device.signal.Signal`, which is a different thing entirely): fires
once, says how it ended (fired, timed out, interrupted), and calls
`on_settle` so a registry can publish the outcome from whichever thread
settled it. Not an extension point — user logic belongs in an `Activity`.
The rig's `Triggers` registry (`flyball.rig.triggers`) gives a trigger a
name and a message so the server can list, fire or interrupt it; on the
wire these are "activities" (`/api/activities`).

## Config

`Config[T]` with `build() -> T`; `ConfigOr[T]` and `resolve` so any component
can be given either a built object or a description of one. Configs hold
real defaults rather than `None` sentinels, so a serialised config records
what the rig actually did. One type names one kind of thing rig-wide
(`Config.registry`), which is what lets a rig file's `driver:` or a link's
`type:` be resolved without knowing which package defined it.

## Resources

`Resource` and `Operator` form a claim graph answering "who may drive this
right now" — distinct from *mode*, which answers "what is it doing". Whether
the graph survives is open; nothing in the current milestone uses it.
