# HTTP and websocket API

All routes are under `/api`; websockets under `/ws`. Bodies and responses
are JSON. OpenAPI is served at `/docs`.

Everything on the wire is named by **address**: a signal's
(`furnace.zone1`), a namespace's (`hum_sensors.dry`), a device's
(`furnace`), or a controller's, which is the address of the writable
signal it drives (`heaters.heater1`). Addresses are dotted paths with no
slashes, so they sit in one path segment.

## Errors

`{"detail": "<message>"}` with the status from the error's base:
404 `NotFoundError` (an address, a device, a command, a tuning), 409
`ConflictError` (a demand the rig refuses, a signal already spoken for,
a unit mismatch), 422 `UnachievableError` or `ValueError`, 503
`NotReadyError` (no rig, nothing read yet, no default controller) or
`HardwareError`.

## Rig

| | | |
| --- | --- | --- |
| `GET` | `/api/health` | `{ok, rig, uptime_s, devices, controllers, conditions, alarms, waits, recording}`; `devices` is `{name: {running, last_read_ns}}` for each polled device, `controllers` is `{name: mode}`; `conditions` is every device's own (`[{device, kind, level, message, since_ns}]`), then the runtime's (`offline`, `slow` from polling, `write_failed` from a blocking writer); `alarms` is `{warn, alarm, max_level}`: the latest reading on every signal, those outside their `warn` band (amber) or `alarm` band (red, not double-counted as warn), plus conditions at `WARNING` (30, counts as warn) or `ERROR` (40, counts as alarm); `max_level` is `40`/`30`/`0`; `{ok: false, rig: null}` with no rig |
| `GET` | `/api/schema` | `{devices: {name: DeviceSchema}}` |
| `GET` | `/api/clock` | `ClockOut`: `{start_time_ns, now_ns, elapsed_ns, tags, speed}` |
| `GET` | `/api/tunings` | `{tag: LawConfig}` |
| `GET` | `/api/tunings/{tag}` | `LawConfig` |
| `PUT` | `/api/tunings/{tag}` | body `LawConfig`; replaces the tuning on the live rig |

A `LawConfig` is `{tag, ...gains}`, e.g. `{"tag": "PI", "kp": 0.5, "ki": 0.05, "tt": 0}`.

## Devices

Every device has one name rig-wide, whatever its driver; `/api/devices`
lists them all with their signal trees.

| | | |
| --- | --- | --- |
| `GET` | `/api/devices` | `[DeviceOut]` |
| `GET` | `/api/devices/{name}` | `DeviceOut`; 404 if no device has that name |
| `GET` | `/api/devices/{name}/schema` | the `DeviceSchema` |
| `POST` | `/api/devices/{name}/commands/{tag}` | body: the command's arguments; returns what the method returns; a command that succeeds on an offline device restarts its polling |
| `POST` | `/api/devices/{name}/restart` | poll an offline device again on its period; `DeviceOut` |
| `PUT` | `/api/devices/{name}/demand` | body `{name: value, ...}`, names relative to the device (dotted under a namespace: `position.x`), values in each signal's unit; one demand, committed at once; returns `{address: WriteOut}` for each signal set; 409 for a signal a controller drives, a `together` group set in part, or a signal that is not writable; 404 for a name not under the device |
| `PUT` | `/api/signals/{address}` | body a number: the single-signal demand; returns `{address: WriteOut}`; 409 if the address is a namespace |

A `DeviceOut` is `{name, label, kind, driver, type, link, poll_s, signals,
commands, state, conditions, run}`: `kind` is `device`, or `simulation`
for an application's own simulation device (see [Simulation](#simulation)),
`driver` the rig file's tag (null for a device built in code), `type` the
class, `link` the rig file's name
for the link it was built on (or null), `signals` the tree, `commands`
`[{name, description, simulation}]`, `state` what the device reports of
itself, `conditions` its own plus the runtime's (`offline`, `slow`), and
`run` `{period_s, running, last_read_ns}` for a polled device (null
otherwise).

A signal in the tree is `{name, address, access, label, quantity, unit,
dimension, dtype, shape, range, precision, warn, alarm, poll_s, limits,
together, latest, write}`: `access` is the set in force as letters (`rp`,
`w`, `rw`, `rpw`), `latest` `{time_ns, value}` once it has been read (null
before), `write` a `WriteOut` for a writable signal once it has been set. A
namespace is `{name, address, atomic, label, poll_s, signals: [...]}`,
nesting the same shapes.

A `WriteOut` is `{value, requested, at_limit, controller}`: what was last
set after limits, what was asked for when the clamp changed it, `low` /
`high` when the value sits on a limit, and the controller driving the
signal (it refuses manual demands; set its reference or detach it).

A `DeviceSchema` is `{name, label, type, driver, description, config,
settings, state, signals, commands: {tag: {description, arguments,
simulation}}}`, each of `config`/`settings`/`state`/`arguments` a JSON
Schema; `signals` is `{path: {address, access, label, quantity, unit,
dimension, range, precision, limits}}` by path relative to the device.

## Reading

| | | |
| --- | --- | --- |
| `GET` | `/api/read/{address}?fresh=` | what the address names: a signal → `{reading: {signal, time_ns, value}}`; an atomic namespace → `{sample: {node, time_ns, values}}`, `values` keyed relative to the node; a device or a namespace read over several transactions → `{samples: [...]}`; `fresh=true` reads the hardware first, which is how a setting (`rw`, never published) is read; 503 until the first read, 404 for an unknown address |
| `GET` | `/api/read?at=a,b,c&fresh=` | several addresses at once, a list in the order given; a fresh read costs each device one read |

## Controllers

A controller binds one publishing signal (`source`) to one writable
signal (`target`) through a law and a feedforward, and is named by its
target's address.

| | | |
| --- | --- | --- |
| `GET` | `/api/controllers` | `[ControllerOut]` |
| `GET` | `/api/controllers/default` | `ControllerOut`; 503 when there is none |
| `GET` | `/api/controllers/{address}` | `ControllerOut` |
| `GET` | `/api/controllers/schema` | what a form needs to make a controller: `sources` and `targets` (`[{address, device, label, unit, dimension, range, limits}]`: every publishing signal, every writable one), `laws`, `feedforwards` and `generators` (JSON Schema unions on `tag`), `tunings` (`{name, law, config}`), `regulated` (`{source: controller}`), `driven` (`{target: controller}`) |
| `POST` | `/api/controllers` | `{target, source, law?, feedforward?, default?, min_period_s?}`; 201 `ControllerOut`; 409 if the target is already driven or the source already regulated, or `feedforward: "setpoint"` across units; 404 for an unknown address |
| `DELETE` | `/api/controllers/{address}` | 204; put in manual first, so the target holds its last demand; manual demands may drive it again |
| `POST` | `/api/controllers/{address}/regulate` | `{at, tuning?, transfer?}`; `at` a value, `process`/`setpoint`/`demand`, or a generator spec (`{tag, ...its own arguments}`, e.g. `{tag: "linear_ramp_setpoint", pace, end}`, discriminated by `tag` against the `generators` union); a generator starts from the controller's current setpoint, or its last reading if it has none yet; `demand` is converted back to the source's unit through the feedforward's inverse, 422 if it has none; the handover's demand is committed at once; 503 if a generator is given and there is neither a setpoint nor a reading to start it from |
| `POST` | `/api/controllers/{address}/manual` | stop regulating; the target keeps its last demand |
| `PUT` | `/api/controllers/{address}/reference` | `{at}`; move the setpoint, or start following a generator spec (as `regulate` takes), without touching the mode |

A `ControllerOut` is `{name, label, target, source, default, mode, law,
feedforward, demand_unit, reference, setpoint, arrived, correction, demand,
expected, delivered_correction, reading}`: `name` is `target`; `label` the
target signal's; `reference` is a number or, mid-trajectory, `{tag,
...the generator's own arguments, end_time?}` (`end_time` in seconds from
the rig's start, once started and unless endless), `setpoint` the value it
resolved to at the last tick (in the source's unit), `arrived` whether the
reference has landed (a number has; a generator once it finishes, judged
in rig time), and `demand`, `expected` and `correction` are in
`demand_unit` -- the target's unit, which the `feedforward` (`{tag:
setpoint | none | affine | table, ...}`, `affine`/`table` taking an
optional `rate_gain` for a ramp's rate of change) maps the setpoint into;
`reading` is `{signal, time_ns, value}` on the source at the last tick.

The generators, by `tag`:

| tag | arguments | on the wire once started |
|---|---|---|
| `linear_ramp_setpoint` | `pace` (a rate, `{per_minute: 10}`, or a duration for the whole walk), `end` | `end_time`; starts from the current setpoint or reading and walks to `end`, so a ramp to where it already is finishes at once; a descending ramp's rate is negative |
| `hold` | `value`, `duration?` | `end_time` when it has a duration; without one it never finishes |
| `profile` | `segments`: a list of generator specs (this union, recursively) | `segments` as given, `active` (the index of the segment in force at the last tick), `end_time` unless the last segment is endless; each segment starts where the previous landed (a ramp's `end`, a hold's `value`), the first from the profile's own start; 422 if a segment before the last never ends, or there are none |

## Waits

What the rig is waiting on: a program step's prompt, a settle test, a hold.

| | | |
| --- | --- | --- |
| `GET` | `/api/waits` | `{name: WaitState}` |
| `GET` | `/api/waits/{name}` | `WaitState` |
| `POST` | `/api/waits/{name}/fire` | `{name, fired: bool}`; false if already settled |
| `POST` | `/api/waits/{name}/interrupt` | `{name, interrupted: bool}` |

A `WaitState` is `{name, message, outcome, since_ns, timeout_s, prompt}`
with `prompt` true for a wait only a person answers (a program's `wait`),
false for a hold or an arrival that settles by itself, and `outcome` one
of `pending`, `fired`, `timeout`, `interrupted`.

## History

Reads the store, never the rig.

| | | |
| --- | --- | --- |
| `GET` | `/api/history/sessions?limit=` | `[SessionRow]`, newest first |
| `GET` | `/api/history/sessions/{id}` | `SessionRow` |
| `DELETE` | `/api/history/sessions/{id}` | 204; everything the session recorded goes; tunings survive |
| `GET` | `/api/history/sessions/{id}/devices` | `[DeviceRow {id, address, driver, config, label}]` |
| `GET` | `/api/history/sessions/{id}/signals` | `[SignalRow {id, device_id, address, quantity, unit, access, dtype, shape, label, range, precision, warn, alarm, limits}]` |
| `GET` | `/api/history/sessions/{id}/writes` | `[WriteRow {signal: SignalRow, driver, limits}]` |
| `GET` | `/api/history/sessions/{id}/controllers` | `[ControllerRow {name, source, law, feedforward}]` |
| `GET` | `/api/history/sessions/{id}/series/{address}` | `Series {signal: SignalRow, points, downsample}`; query `start_ns`, `end_ns`, and one of `every`, `bucket_ns`, `max_points` |
| `GET` | `/api/history/sessions/{id}/writes/{address}` | `[WriteStateRow {offset_ns, value, requested, at_limit, controller}]`; query `start_ns`, `end_ns` |
| `GET` | `/api/history/sessions/{id}/ticks/{controller}` | `[Tick]`; query `start_ns`, `end_ns` |
| `GET` | `/api/history/sessions/{id}/events` | `[Event]`; query `start_ns`, `end_ns` |
| `GET` | `/api/history/sessions/{id}/spans` | `[Span]`, in start order; nest by `parent_id` |
| `GET` | `/api/history/sessions/{id}/export?format=csv\|json\|zip&layout=wide\|long&step_s=` | the session as a file: `wide` one column per signal (each row holds every signal's last value; `step_s` resamples onto a grid), `long` one row per value (`device, signal, unit, value`), `zip` both (`signals-wide.csv`, `signals-long.csv`) plus each controller's ticks (`controller-{name}.csv`), each write's states (`write-{address}.csv`), `events.csv` and `session.json` (`devices`, `signals`, `controllers`) |
| `GET` | `/api/history/sessions/{id}/series/{address}/export?format=` | one signal as csv/json |
| `GET` | `/api/history/sessions/{id}/writes/{address}/export?format=` | one signal's write states as csv/json |
| `GET` | `/api/history/sessions/{id}/ticks/{controller}/export?format=` | one controller's ticks as csv/json |
| `GET` | `/api/history/sessions/{id}/events/export?format=` | the events as csv/json |
| `GET` | `/api/history/tunings` | `[TuningRow]`, newest version of every name |
| `GET` | `/api/history/tunings/{name}` | `TuningRow` |
| `GET` | `/api/history/tunings/{name}/history` | `[TuningRow]`, newest first |
| `PUT` | `/api/history/tunings/{name}` | body `{law, config, created_ns, session_id?, controller?, notes?}`; 201; adds a version |
| `DELETE` | `/api/history/tunings/{name}` | 204; every version |

Times in history are integer nanosecond offsets from the session's start.

## Programs

A program is a document in the server's [dialect](../3-running/programs.md);
`check` and `run` take it as the body. Steps name a controller by its
target address (or none for the rig's default), a device by name.

| | | |
| --- | --- | --- |
| `GET` | `/api/programs/schema` | JSON Schema for a program file in the dialect |
| `GET` | `/api/programs/commands` | the internally tagged request union as JSON Schema |
| `POST` | `/api/programs/check` | normalise and validate a document; 422 names the step that fails to parse; else `ProgramCheck {ok, error?, normalised, warnings}` |
| `POST` | `/api/programs/run?interrupt=` | start a document; returns `ProgrammerState` once the first step is applied |
| `POST` | `/api/programs/command?interrupt=` | one internally tagged command |
| `GET` | `/api/programs/running` | `ProgrammerState` |
| `POST` | `/api/programs/interrupt` | stop whatever is running |
| `GET` | `/api/programs/library/{name}/check` | `ProgramCheck`, the same shape, for the newest stored version |

`ProgramCheck` is `{ok, error?, normalised?, warnings}`: `warnings` maps a step
index to a message naming a controller, tuning or device the rig lacks right
now (`Program.missing`) -- advice, not a refusal, since the rig may gain it
before the program runs; a document that fails to parse or validate instead
has `ok: false` and `error` set, with no `normalised` or `warnings`.

## Dashboards

What the UI shows and how, saved per rig. The server keeps every version
under a name, as it does for programs; the document's `widgets` are the
UI's to define, validated only in outline (`{id, kind, title?, x, y, w, h,
config}` on a `grid` of `cols` (12 or 24) × `row_height`). A rig can ship
`dashboards/*.json` beside its file; they are imported on start.

| | | |
| --- | --- | --- |
| `GET` | `/api/dashboards?every=` | `[DashboardRow]`, newest version of each name, this rig's unless `every` |
| `GET` | `/api/dashboards/schema` | JSON Schema of the document |
| `GET` | `/api/dashboards/{name}` | `DashboardWithProblems` |
| `GET` | `/api/dashboards/{name}/history` | `[DashboardRow]`, newest first |
| `PUT` | `/api/dashboards/{name}` | body the document; 201 `DashboardWithProblems`; adds a version; `name` and `rig` are set from the key and the rig |
| `POST` | `/api/dashboards/{name}/rename` | `{name}`; every version moves; 409 if taken |
| `DELETE` | `/api/dashboards/{name}` | 204; every version |

A `DashboardRow` is `{id, name, rig, body, created_ns, sha256}`; a
`DashboardWithProblems` is the same plus `problems: [{widget_id, ref,
reason}]` — every widget whose binding (a `readout`/`gauge`'s `address`, a
`chart`'s `addresses`, a `loop`'s `controller`, a `device`'s `device`, all
inside the widget's own `config`) names something this rig does not
currently have; a readout wants a signal that publishes. The document is
saved and returned as given; nothing is refused for this.

Documents carry `schema_version: 2`. A version-1 document (bindings to
channels, loops and actuators) is migrated on read, never refused, and
what is stored stays as saved: `channel` (`"source.measurand"` or
`{source, measurand}`) becomes `address`, `channels` become `addresses`,
a `loop` widget's `loop` becomes `controller`, and an `actuator` widget
becomes a `device` widget bound by `device`. A loop was named by its
actuator and a controller by its target's address, so a migrated `loop`
binding may show as a problem until it is rebound.

## Simulation

Only a rig whose links are all `sim_*`/`fake_*`; every route but the first answers 409 otherwise.

| | | |
| --- | --- | --- |
| `GET` | `/api/sim` | `{simulated, device, name, path, clock: {speed, measured, stepped, now_ns}, plants: {name: {config, links, live, readings, stats, input, output}}, changed}`; `inputs`/`outputs` for a multi-port plant; `readings` keyed by signal address; `device` says whether `/api/sim/device` exists (an application's own simulation device, e.g. a `disturb`), so a client need not probe it and 404 on a rig without one |
| `PUT` | `/api/sim/clock` | `{speed}`; the rig's time runs at `speed`× from now on; 409 on a stepped clock |
| `POST` | `/api/sim/clock/step` | `{seconds}`; a stepped clock only |
| `GET` | `/api/sim/plants/{name}` | a plant's config and state |
| `PUT` | `/api/sim/plants/{name}` | some of its parameters, changed live |
| `POST` | `/api/sim/plants/{name}/reset` | `{output?, input?}` |
| `GET` | `/api/sim/config` | the rig file as it now stands |
| `POST` | `/api/sim/save` | `{path?}`; writes it, default where it was loaded from |
| `GET` | `/api/sim/device` | the application's simulation device: `{config, settings, state}`; 404 without one |
| `GET` | `/api/sim/device/schema` | its `DeviceSchema` |
| `POST` | `/api/sim/device/{command}` | one of its commands |

`GET /api/clock` carries `speed` too, so a client can label a time axis.

## Events

| | | |
| --- | --- | --- |
| `GET` | `/api/events?limit=&level=` | the last few hundred `Event`s, oldest first; `level` keeps that level and above |

An `Event` is `{time_ns, level, scope, subject, kind, message, details}`;
`level` is `DEBUG`, `INFO`, `WARNING` or `ERROR`.

## Websockets

Every socket sends what the rig knows on connect, then every 50 ms one
frame of whatever changed: the rig keeps only the newest value per key,
so a socket costs at most one frame per flush at any tick rate. An empty
flush sends nothing.

| socket | on connect | then |
| --- | --- | --- |
| `/ws/samples` | the newest published sample per node | `{samples: [{node, time_ns, values}]}` of the nodes that delivered; only publishing signals, `values` keyed relative to `node`; at most one sample per node per flush |
| `/ws/writes` | every write state | `{writes: [{signal, value, requested, at_limit, controller}]}` of the signals committed |
| `/ws/controllers` | every controller | `{controllers: [ControllerOut]}` of those that ticked |
| `/ws/devices` | every polled device | `{devices: [{name, period_s, running, last_read_ns, conditions, state}]}` as each reads, fails or is restarted |
| `/ws/waits` | every registered wait | `{waits: [WaitState]}` as each registers or settles |
| `/ws/events` | the recent events | `{events: [Event]}` as each happens |
