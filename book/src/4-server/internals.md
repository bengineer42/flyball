# Server

`flyball.interfaces.server` is a FastAPI app over a rig and a store. It owns no
hardware and no database: whatever builds them calls `set_rig` and
`set_store` before serving, so the same app runs against a real rig, a
simulation, or a database copied from another machine.

## Assembly

`create_app(auth, root_path, *, front, port, login_delay, open_network)`
adds `/docs` (Swagger UI from the vendored `server/swagger/` --
`swagger-ui-dist` 5.33.0, Apache-2.0 -- mounted at `/docs/assets`, no CDN),
CORS, one exception handler per error base, the routers, the built
dashboard (a bare runner only, when one is installed), and then --
outermost -- two plain ASGI middlewares: the door, `Door`
(`server/auth.py`), and, when asked, `RootPath` (sets `scope["root_path"]`
under the prefix so Starlette routes and links as at the root; 404 / 4404
elsewhere; lifespan passes through). `app` is a module-level instance for
`uvicorn flyball.interfaces.server:app`. Routes take the rig and store
through dependencies (`RigDep`, `StoreDep`), which raise `NotReadyError`
(503) when nothing is attached.

The door puts one principal on every request (`request.state.principal`,
a `principal.Claims`, and how it got in on `request.state.scheme`) and
admits it if `verbs.allows(claims.scp, scope)`: the verb table in
`server/verbs.py` names what each route needs, and a route with no row is
refused (`403`, `needed: null`). It works in one of two modes:

- **fronted** (`front=Fronted(key, aud)`, from `flyball-runner --front-dir`):
  the `X-Flyball-Principal` the front signed is the only credential. None,
  two, one that does not verify, or any other `x-flyball-*` header in any
  spelling is `401` with `X-Flyball-Principal-Error: <code>` (a socket is
  accepted, then closed with 4401). `runner.auth`, bearer tokens, cookies
  and `?token=` are ignored; `Host` and `Origin` are the front's.
- **bare** (no front): `runner.auth.token` is the one credential -- a bearer
  token, or a session cookie traded for it (`routes/auth.py`: the pasted
  token, or the one-time link). The door makes principals in the same
  shape as the front's, with an in-memory key and audience (`bare-<8 hex>`).
  With no token it is open: every verb, loopback `Host` names only unless
  `open_network`. In every bare mode a request that acts with a foreign or
  `null` `Origin` and no token is `403`, and `?token=` is `401`.

A principal lacking the route's verb is `403` `{detail, needed}` (4403); an
anonymous one gets `401` instead, so the UI offers sign-in. At a front the
runner cannot tell `401` itself (that means a bad principal, a `502`), so
for the front's `anon:` visitor it answers `403` -- and refuses a socket's
upgrade outright rather than accepting it -- and the front turns that into
`401`, or a socket closed with 4401. A caller with no verb on the rig at
all never reaches the runner: the front gives the same answer itself. How the front
and the runner share a key and verify each other is in
[The front and the runner](../6-internals/front.md).

Between the door and `RootPath` sits `Audit` (`server/audit.py`): each
request that acts -- its verb neither read nor open -- and carries a verified
principal that is not anonymous is one row (a caller's refusals at most ten a
minute) in the store's append-only `audit` table, written off
the loop; an audit write that fails is logged and refuses nothing
([Storage](../6-internals/db.md#sqlite)).

The store is synchronous and serialised by one lock
([Storage](../6-internals/db.md#sqlite)), so a route that touches it is a plain
`def` — FastAPI runs it on a worker thread — or, when it must be `async` (to
read a request body), hands the store call to `anyio.to_thread`. `StoreDep`
holds one of a few `STORE_SLOTS` for the request, so store requests queued
behind a long one wait on the loop, not on worker threads. `async def` is for
routes and websockets that never reach the store or the rig's lock: they
read the rig's dicts through C-level `list(...)` copies instead (`/api/health`,
the controllers routes, the websockets' first frame), and the simulation's
reads, which do take it, are plain `def`. The suite fails any test in which
the app's loop took the rig's lock, as it does for the store's. The
same holds for any other slow, blocking work. `POST /api/rig/stop` runs its
stop on a limiter of its own (four threads), so a stop never queues behind
the worker threads other routes share.

`server/routes/` is one module per concern, not per device:

| module | serves |
| --- | --- |
| `rig.py` | the live rig read-only — health, clock, tunings; what the rig is *made of* is elsewhere |
| `runner.py` | `/api/runner*`: the process's resolved settings, shutdown and restart, through the handle `flyball-runner` sets with `set_runner` (404 without one) |
| `auth.py` | `/api/auth*`: AuthInfo v2, a bare runner's token sign-in, logout and one-time link, and the fronted runner's readiness probe `/api/auth/front` |
| `stop.py` | `POST /api/rig/stop`: calls the `Stopper` (`deps.current_stopper()`) and answers its `StopReport` |
| `devices.py` | `/api/devices*`: the tree, commands, demands |
| `read.py` | `/api/read*`: readings, samples, fresh reads |
| `controllers.py` | `/api/controllers*`: wiring, regulate/manual, reference |
| `activities.py` | `/api/activities*` |
| `events.py` | `/api/events` |
| `telemetry.py` | the websockets: `/ws/samples` (a demand's write record and a device's run ride along with it), `/ws/controllers`, `/ws/activities`, `/ws/events` |
| `recording.py` | starting and stopping recording on the live rig — the one place the rig and the store meet |
| `history.py` | reads the store: sessions, devices, signals, writes, controllers, series, ticks, events, spans, exports |
| `export.py` | the file forms `history.py`'s export endpoints share |
| `program.py` | check/run/cancel against the live rig |
| `library.py` | the program library: documents kept as written (YAML/TOML/JSON), versioned by name, read back in another format on demand |
| `dashboards.py` | dashboard documents, versioned per rig, migrated on read |
| `sim.py` | `/api/sim*`, only on a rig whose links are all `sim_*`/`fake_*` |
| `schema.py` | `/api/schema` |

## Resolution at request time

Device routes are `/{name}` and `/{name}/commands/{command}`, resolved against
the live rig per request rather than mounted per device, because the app
exists before the rig is set and a device may be attached later. The price
is that OpenAPI lists one generic command route; `/api/devices/{name}/schema`
carries the real request schema for each command, which is what a form or
the CLI reads anyway.

## Wire models

`flyball.interfaces.server.schemas` holds the shapes that cross the wire, separate from
the domain types so the HTTP surface and the control code can change shape
independently.

| model | of | shape |
| --- | --- | --- |
| `SignalOut` | a `Signal` in a device's tree | `{name, address, access, role, tags, label, quantity, unit, dimension, dtype, shape, range, precision, warning, alarm, poll_s, stale_after_s, limits, initial, quality, readback, on_no_value, latest, last_usable, write}`; `stale_after_s` from `rig.liveness.threshold_s` |
| `NamespaceOut` | a `Node` | `{name, address, atomic, label, poll_s, signals: [...]}`, nesting `SignalOut`/`NamespaceOut` |
| `WriteOut` | a `WriteState` | `{value, requested, at_limit, controller}` -- a signal's `write` (`GET /api/devices`) only, now |
| `WriteMetaOut` | a demand's `Reading` | `{requested, at_limit, controller}` -- `WriteOut` without `value`, already in `SampleOut.values` |
| `SampleOut` | a `Sample`, plus `rig.latest` for each demand's write record | `{node, time_ns, values, writes}`, both keyed relative to `node`; `writes` only for the demands the sample includes |
| `ReadingOut` | a `Reading` | `{signal, time_ns, value}` |
| `CommandOut` | a `CommandSpec` | `{name, description, simulation, commit, mode, interrupts, writes, demand_of, links}` |
| `CommandRunOut` | a `CommandRun` (`Rig.invoke`) | `{result, interrupted: [{controller, was}]}` |
| `DeviceOut` | a `Device` | `{name, label, kind, driver, class_name, link, poll_s, signals, commands, inputs, consumers, sources, readable, writable, conditions, run}`; `inputs` from each `InputBinding` on `device.bound`, `consumers` from `Rig.consumers`, `sources` from `Rig.values.source` |
| `ControllerOut` | a `Controller`/`ControllerView` | identity, mode, law config and state, reference, setpoint, correction, demand, expected, reading |
| `ClockOut` | a `Clock` | `{start_time_ns, now_ns, elapsed_ns, tags, speed}` |

Law configs cross as a `type`-discriminated union built from the registry, so
a law added by a package is accepted without a change here. `book/src/4-server/api.md`
is the wire's own reference; read it for the full shape of each — this page
is about where the code that builds them lives.

## Command requests

Each device command's request model is derived from the method's signature
(`flyball.interfaces.server.wire`): one field per parameter after `self`, with domain
types that cannot cross the wire swapped for wire ones through `WIRE_TYPES`
(a running control law becomes a `LawConfig | str | None` — a config or the
name of a stored tuning). `POST /api/devices/{name}/commands/{command}` validates
the body against it and calls the method with the result. Program steps
do the same from their dataclass constructor.

## Telemetry

The rig publishes nothing itself; it only fills `Latest` cells (samples by
node address, write states by signal address -- kept for the recorder and
`device.written`, not streamed on its own any more -- controller states and
device runs by name, activities by name) and a `Topic` of published samples,
each built only while something is watching. `telemetry.py`'s sockets read
those cells directly — no separate observer is attached for them.

| socket | cell(s) | frame |
| --- | --- | --- |
| `/ws/samples` | `rig.samples` (newest published sample per node), `rig.latest` (for `writes`), `rig.polling.runs` (for `runs`; `conditions` read from `rig.conditions` as each run is sent, and a device-scope edge re-sets its run via `Polling.touch`) | `{samples?: [SampleOut], runs?: [{name, period_s, running, last_read_ns, read_s, missed, reading_since_ns, consecutive_failures, next_retry_ns, conditions}]}`; either key present only when something in it changed, at most one sample per node and one run per device per flush |
| `/ws/controllers` | `rig.controller_states` joined to controller settings | `{controllers: [ControllerOut]}` of those that ticked |
| `/ws/activities` | `rig.triggers.latest` | `{activities: [ActivityOut]}` as each registers or settles |
| `/ws/events` | `rig.recent` | `{events: [Event]}` as each happens |

`/ws/samples` folds in what `/ws/writes` and `/ws/devices` used to carry
separately: `Rig._states` (the write path, `runtime/rig.py`) folds a
demand's clamp/controller into the `Reading` it leaves in `rig.latest`
(`replace`d in place, after the ordinary push), so `SampleOut.of` can read
it back off `rig.latest` for any demand a sample includes, with no extra
cell of its own to watch; `_flush_samples` merges `rig.samples` and
`rig.polling.runs` into one frame instead of `_flush`'s one-cell loop.

Each socket sends everything on connect, then every 50 ms (`FLUSH_S`)
one frame of whatever changed; an empty flush sends nothing. Nothing is
sent to a quiet socket, so only receiving notices a disconnect; a reader
task runs beside the push loop for that.

## The program dialect

`flyball.interfaces.server.dialect` is the bridge between the file form a person writes
(externally tagged, shorthand, flat time keys, modifiers) and the internally
tagged form pydantic validates. `normalise_step` rewrites one; `step_schema`
and `program_schema` emit the file's JSON schema from the same command
registry. A step names a controller by its target's address (or none, for
the rig's default) and a device by name — never a source or an actuator.
[Writing programs](../1-running/programs/writing.md) has the rules.
