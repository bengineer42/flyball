# Server

`flyball.server` is a FastAPI app over a rig and a store. It owns no
hardware and no database: whatever builds them calls `set_rig` and
`set_store` before serving, so the same app runs against a real rig, a
simulation, or a database copied from another machine.

## Assembly

`create_app()` adds CORS, one exception handler per error base, the health
route, and the routers. `app` is a module-level instance for
`uvicorn flyball.server:app`. Routes take the rig and store through
dependencies (`RigDep`, `StoreDep`), which raise `NotReadyError` (503) when
nothing is attached.

## Resolution at request time

Device routes are `/{name}` and `/{name}/{command}`, resolved against the
live rig per request rather than mounted per device, because the app exists
before the rig is set and a device may be attached later. The price is that
OpenAPI lists one generic command route; `/{name}/schema` carries the real
request schema for each command, which is what a form or the CLI reads
anyway.

## Wire models

`flyball.server.schemas` holds the shapes that cross the wire, separate from
the domain types so the HTTP surface and the control code can change shape
independently.

| model | of | shape |
| --- | --- | --- |
| `ChannelOut` | `Channel` | `{source, measurand, unit, label, range, precision}` |
| `SampleOut` | `Sample` | `{seq, time_ns, values: {measurand: value}}` |
| `SourceOut` | `Source` | `{name, channels, latest}` |
| `LoopOut` | `LoopView` | identity, mode, law config and state, reference, correction, demand, expected, reading |
| `ClockOut` | `Clock` | `{start_time_ns, now_ns, elapsed_ns, tags}` |

Law configs cross as a `tag`-discriminated union built from the registry, so
a law added by a package is accepted without a change here.

## Command requests

Each device command's request model is derived from the method's signature
(`flyball.server.wire`): one field per parameter after `self`, with domain
types that cannot cross the wire swapped for wire ones through `WIRE_TYPES`.
`run` validates the body against it and calls the method with the result.
Program commands do the same from the dataclass constructor.

## Telemetry

The rig publishes nothing itself. `set_rig` attaches one `Telemetry`
observer that turns every sample into a `SampleOut` and fans it out through
a `Topic`; with nobody connected the topic drops it. The other sockets read
`Latest` cells:

| socket | cell | frame |
| --- | --- | --- |
| `/ws/samples` | topic | every sample, as published; oldest dropped if the client lags |
| `/ws/loops` | `rig.loop_states` joined to loop settings | `{loops: [LoopOut…]}` |
| `/ws/actuators` | `rig.actuator_states` | `{actuators: [{name, state}…]}` |
| `/ws/readers` | reader runs | `{readers: [{name, run}…]}` |
| `/ws/signals` | signal outcomes | `{signals: [{name, state}…]}` |

Each cell socket sends everything on connect, then every 50 ms one frame of
whatever changed; an empty flush sends nothing. Nothing is sent to a quiet
socket, so only receiving notices a disconnect; a reader task runs beside
the push loop for that.

## The program dialect

`flyball.server.dialect` is the bridge between the file form a person writes
(externally tagged, shorthand, flat time keys, modifiers) and the internally
tagged form pydantic validates. `normalise_step` rewrites one; `step_schema`
and `program_schema` emit the file's JSON schema from the same command
registry. [Writing programs](../3-running/programs.md) has the rules.
