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

`server/routes/` is one module per concern, not per device:

| module | serves |
| --- | --- |
| `rig.py` | the live rig read-only — health, clock, tunings; what the rig is *made of* is elsewhere |
| `devices.py` | `/api/devices*`: the tree, commands, demands |
| `read.py` | `/api/read*`: readings, samples, fresh reads |
| `controllers.py` | `/api/controllers*`: wiring, regulate/manual, reference |
| `waits.py` | `/api/waits*` |
| `events.py` | `/api/events` |
| `telemetry.py` | the websockets: `/ws/samples`, `/ws/writes`, `/ws/controllers`, `/ws/devices`, `/ws/waits`, `/ws/events` |
| `recording.py` | starting and stopping recording on the live rig — the one place the rig and the store meet |
| `history.py` | reads the store: sessions, devices, signals, writes, controllers, series, ticks, events, spans, exports |
| `export.py` | the file forms `history.py`'s export endpoints share |
| `program.py` | check/run/interrupt against the live rig |
| `library.py` | the program library: documents kept as written (YAML/TOML/JSON), versioned by name, read back in another format on demand |
| `dashboards.py` | dashboard documents, versioned per rig, migrated on read |
| `sim.py` | `/api/sim*`, only on a rig whose links are all `sim_*`/`fake_*` |
| `schema.py` | `/api/schema` |

## Resolution at request time

Device routes are `/{name}` and `/{name}/commands/{tag}`, resolved against
the live rig per request rather than mounted per device, because the app
exists before the rig is set and a device may be attached later. The price
is that OpenAPI lists one generic command route; `/api/devices/{name}/schema`
carries the real request schema for each command, which is what a form or
the CLI reads anyway.

## Wire models

`flyball.server.schemas` holds the shapes that cross the wire, separate from
the domain types so the HTTP surface and the control code can change shape
independently.

| model | of | shape |
| --- | --- | --- |
| `SignalOut` | a `Signal` in a device's tree | `{name, address, access, role, tags, label, quantity, unit, dimension, dtype, shape, range, precision, warn, alarm, poll_s, limits, initial, latest, write}` |
| `NamespaceOut` | a `Node` | `{name, address, atomic, label, poll_s, signals: [...]}`, nesting `SignalOut`/`NamespaceOut` |
| `WriteOut` | a `WriteState` | `{value, requested, at_limit, controller}` |
| `SampleOut` | a `Sample` | `{node, time_ns, values}`, `values` keyed relative to `node` |
| `ReadingOut` | a `Reading` | `{signal, time_ns, value}` |
| `CommandOut` | a `CommandSpec` | `{name, description, simulation, commit, mode, interrupts, demand_of, links}` |
| `DeviceOut` | a `Device` | `{name, label, kind, driver, type, link, poll_s, signals, commands, inputs, readable, writable, conditions, run}` |
| `ControllerOut` | a `Controller`/`ControllerView` | identity, mode, law config and state, reference, setpoint, correction, demand, expected, reading |
| `ClockOut` | a `Clock` | `{start_time_ns, now_ns, elapsed_ns, tags, speed}` |

Law configs cross as a `tag`-discriminated union built from the registry, so
a law added by a package is accepted without a change here. `book/src/6-reference/api.md`
is the wire's own reference; read it for the full shape of each — this page
is about where the code that builds them lives.

## Command requests

Each device command's request model is derived from the method's signature
(`flyball.server.wire`): one field per parameter after `self`, with domain
types that cannot cross the wire swapped for wire ones through `WIRE_TYPES`
(a running control law becomes a `LawConfig | str | None` — a config or the
name of a stored tuning). `POST /api/devices/{name}/commands/{tag}` validates
the body against it and calls the method with the result. Program commands
do the same from their dataclass constructor.

## Telemetry

The rig publishes nothing itself; it only fills `Latest` cells (samples by
node address, write states by signal address, controller states and device
runs by name, waits by name) and a `Topic` of published samples, each built
only while something is watching. `telemetry.py`'s sockets read those cells
directly — no separate observer is attached for them.

| socket | cell | frame |
| --- | --- | --- |
| `/ws/samples` | `rig.samples` (newest published sample per node) | `{samples: [SampleOut]}`; at most one sample per node per flush |
| `/ws/writes` | `rig.write_states` | `{writes: [WriteOut with signal]}` of the signals committed |
| `/ws/controllers` | `rig.controller_states` joined to controller settings | `{controllers: [ControllerOut]}` of those that ticked |
| `/ws/devices` | `rig.polling.runs` | `{devices: [{name, period_s, running, last_read_ns, conditions}]}` as each reads, fails or is restarted |
| `/ws/waits` | `rig.triggers.latest` | `{waits: [WaitState]}` as each registers or settles |
| `/ws/events` | `rig.recent` | `{events: [Event]}` as each happens |

Each cell socket sends everything on connect, then every 50 ms (`FLUSH_S`)
one frame of whatever changed; an empty flush sends nothing. Nothing is
sent to a quiet socket, so only receiving notices a disconnect; a reader
task runs beside the push loop for that.

## The program dialect

`flyball.server.dialect` is the bridge between the file form a person writes
(externally tagged, shorthand, flat time keys, modifiers) and the internally
tagged form pydantic validates. `normalise_step` rewrites one; `step_schema`
and `program_schema` emit the file's JSON schema from the same command
registry. A step names a controller by its target's address (or none, for
the rig's default) and a device by name — never a source or an actuator.
[Writing programs](../3-running/programs.md) has the rules.
