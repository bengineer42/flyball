# HTTP and websocket API

All routes are under `/api`; websockets under `/ws`. Bodies and responses
are JSON. OpenAPI is served at `/docs`.

## Errors

`{"detail": "<message>"}` with the status from the error's base:
404 `NotFoundError`, 409 `ConflictError`, 422 `UnachievableError` or
`ValueError`, 503 `NotReadyError` or `HardwareError`.

## Rig

| | | |
| --- | --- | --- |
| `GET` | `/api/health` | `{ok, rig, uptime_s, readers, loops, conditions, alarms, signals, recording}`; `conditions` includes readers' (`offline`, `slow`), actuators' own, and writers' `write_failed`; `alarms` is `{warn, alarm, max_level}`: every source's latest sample, channels outside their `warn` band (amber) or `alarm` band (red, not double-counted as warn), plus device conditions at `WARNING` (30, counts as warn) or `ERROR` (40, counts as alarm); `max_level` is `40`/`30`/`0`; `{ok: false, rig: null}` with no rig |
| `GET` | `/api/schema` | `{actuators: {name: DeviceSchema}, readers: {name: DeviceSchema}}` |
| `GET` | `/api/clock` | `ClockOut` |
| `GET` | `/api/sources` | `[SourceOut]` |
| `GET` | `/api/sources/{name}` | `SourceOut` |
| `GET` | `/api/sources/{name}/{measurand}` | `{channel, time_ns, value}`; 404 until the first delivery |
| `GET` | `/api/loops` | `[LoopOut]` |
| `GET` | `/api/loops/default` | `LoopOut` |
| `GET` | `/api/loops/{name}` | `LoopOut` |
| `GET` | `/api/loops/schema` | what a form needs to make a loop: `channels` (with `dimension`), `actuators` (`demand_unit`, which channels each may drive), `laws` and `feedforwards` (JSON Schema unions on `tag`), `tunings` (`{name, law, config}`), `regulated` |
| `POST` | `/api/loops` | `{channel, actuator, law?, feedforward?, default?, min_period_s?}`; 201 `LoopOut`; 409 if the actuator already drives a loop, or `feedforward: "setpoint"` across units |
| `DELETE` | `/api/loops/{name}` | 204; put in manual first, so the actuator holds its last demand |
| `POST` | `/api/loops/{name}/regulate` | `{at, tuning?, transfer?}`; `at` a value or `process`/`setpoint`/`demand`; `demand` is converted back to the channel's unit through the feedforward's inverse, 422 if it has none |
| `POST` | `/api/loops/{name}/manual` | stop regulating; the actuator keeps its last demand |
| `PUT` | `/api/loops/{name}/reference` | `{at}`; move the setpoint without touching the mode |
| `GET` | `/api/tunings` | `{tag: LawConfig}` |
| `GET` | `/api/tunings/{tag}` | `LawConfig` |
| `PUT` | `/api/tunings/{tag}` | body `LawConfig`; replaces the tuning on the live rig |

A `DeviceSchema` is `{name, label, type, description, config, settings, state,
commands: {tag: {description, arguments, simulation}}}`, each schema a JSON
Schema; actuators add `demand_unit`, readers add `sources`. `label` is the
display name from the rig file, or null.

A `SourceOut` is `{name, label, channels: [ChannelOut], latest}`; `label` is
the source's display name from the rig file, or null (show `name`). A
`ChannelOut` is `{source, measurand, unit, label, range, precision, warn,
alarm, dimension}`; `label` here is the measurand's own display label, always
present.

Every actuator's `state` carries `output_range`: `[min, max]` in
`demand_unit`, or null if not known. A sim actuator works it out from its
drive `limits` (null in "smart" mode, where the actuator models the plant
and the range is not a fixed scale); a real one states it in its config, or
it is null.

A `LoopOut` is `{name, label, channel, default, mode, law, feedforward,
demand_unit, reference, setpoint, correction, demand, expected,
delivered_correction, reading}`: `reference` is a number or the name of the
trajectory being followed, `setpoint` the value it resolved to at the last
tick, and `demand`, `expected` and `correction` are in `demand_unit` -- the
actuator's unit, which the `feedforward` (`{tag: setpoint | none | affine |
table, ...}`, `affine`/`table` taking an optional `rate_gain` for a ramp's
rate of change) maps the setpoint into.

A `LawConfig` is `{tag, ...gains}`, e.g. `{"tag": "PI", "kp": 0.5, "ki": 0.05, "tt": 0}`.
A `SignalState` is `{name, message, outcome, since_ns, timeout_s, prompt}` with
`prompt` true for a wait only a person answers (a program's `wait`), false for a hold
or an arrival that settles by itself, and
`outcome` one of `pending`, `fired`, `timeout`, `interrupted`.

## Devices

The same shape under `/api/actuators` and `/api/readers`.

| | | |
| --- | --- | --- |
| `GET` | `/api/actuators` | `{name: {name, type, description}}` |
| `GET` | `/api/actuators/{name}` | `{config, settings, state}`; `state.output_range` is `[min, max]` in `demand_unit`, or null |
| `GET` | `/api/actuators/{name}/schema` | the `DeviceSchema` |
| `POST` | `/api/actuators/{name}/{command}` | body: the command's arguments; returns what the method returns |
| `POST` | `/api/readers/{name}/restart` | poll an offline reader again on its period; a command that succeeds on an offline reader (`restore`, a reset) restarts it the same way |

A reader's view also carries its `run`: `{period_s, running, last_read_ns,
conditions}`.

Every reader, actuator and application device (see [Simulation](#simulation))
has one name rig-wide; `/api/devices` lists them together instead of by kind.

| | | |
| --- | --- | --- |
| `GET` | `/api/devices` | `{name: {name, label, kind, type, link}}`; `kind` is `reader`, `actuator`, `simulation` (an application's own device) or another an application registers; `link` is the rig file's link name, or null when the device has none (a sim device, e.g.) |
| `GET` | `/api/devices/{name}` | the same shape for one device, whichever kind it is; 404 if no device has that name |

A name collision -- two devices declared with the same name -- is a 409
naming both of them; a rig file with one fails to load in the first place
(see [Rig file schema](rig-file.md)).

## Signals

| | | |
| --- | --- | --- |
| `GET` | `/api/signals` | `{name: SignalState}` |
| `GET` | `/api/signals/{name}` | `SignalState` |
| `POST` | `/api/signals/{name}/fire` | `{name, fired: bool}`; false if already settled |
| `POST` | `/api/signals/{name}/interrupt` | `{name, interrupted: bool}` |

## History

Reads the store, never the rig.

| | | |
| --- | --- | --- |
| `GET` | `/api/history/sessions?limit=` | `[SessionRow]`, newest first |
| `GET` | `/api/history/sessions/{id}` | `SessionRow` |
| `DELETE` | `/api/history/sessions/{id}` | 204; everything the session recorded goes; tunings survive |
| `GET` | `/api/history/sessions/{id}/sources` | `[SourceRow]`; `label` is the source's display name, or null |
| `GET` | `/api/history/sessions/{id}/channels` | `[ChannelRow]` |
| `GET` | `/api/history/sessions/{id}/actuators` | `[ActuatorRow]`; `config` is the actuator's config as built, or null |
| `GET` | `/api/history/sessions/{id}/loops` | `[LoopRow]` |
| `GET` | `/api/history/sessions/{id}/series/{source}/{measurand}` | `Series`; query `start_ns`, `end_ns`, and one of `every`, `bucket_ns`, `max_points` |
| `GET` | `/api/history/sessions/{id}/ticks/{loop}` | `[Tick]`; query `start_ns`, `end_ns` |
| `GET` | `/api/history/sessions/{id}/events` | `[Event]`; query `start_ns`, `end_ns` |
| `GET` | `/api/history/sessions/{id}/spans` | `[Span]`, in start order; nest by `parent_id` |
| `GET` | `/api/history/sessions/{id}/export?format=csv\|json\|zip&layout=wide\|long&step_s=` | the session as a file: `wide` one column per channel (each row holds every channel's last value; `step_s` resamples onto a grid), `long` one row per value, `zip` both plus each loop's ticks, the events and `session.json` |
| `GET` | `/api/history/sessions/{id}/series/{source}/{measurand}/export?format=` | one channel as csv/json |
| `GET` | `/api/history/sessions/{id}/ticks/{loop}/export?format=` | one loop's ticks as csv/json |
| `GET` | `/api/history/sessions/{id}/events/export?format=` | the events as csv/json |
| `GET` | `/api/history/tunings` | `[TuningRow]`, newest version of every name |
| `GET` | `/api/history/tunings/{name}` | `TuningRow` |
| `GET` | `/api/history/tunings/{name}/history` | `[TuningRow]`, newest first |
| `PUT` | `/api/history/tunings/{name}` | body `{law, config, created_ns, session_id?, loop?, notes?}`; 201; adds a version |
| `DELETE` | `/api/history/tunings/{name}` | 204; every version |

Times in history are integer nanosecond offsets from the session's start.

## Programs

A program is a document in the server's [dialect](../3-running/programs.md);
`check` and `run` take it as the body.

| | | |
| --- | --- | --- |
| `GET` | `/api/programs/schema` | JSON Schema for a program file in the dialect |
| `GET` | `/api/programs/commands` | the internally tagged request union as JSON Schema |
| `POST` | `/api/programs/check` | normalise and validate a document; returns it internally tagged; 422 names the step |
| `POST` | `/api/programs/run?interrupt=` | start a document; returns `ProgrammerState` once the first step is applied |
| `POST` | `/api/programs/command?interrupt=` | one internally tagged command |
| `GET` | `/api/programs/running` | `ProgrammerState` |
| `POST` | `/api/programs/interrupt` | stop whatever is running |

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
reason}]` — every widget whose binding (a `readout`/`gauge`'s `channel`, a
`chart`'s `channels`, a `loop`'s `loop`, an `actuator`'s `actuator`, all
inside the widget's own `config`) names something this rig does not
currently have. The document is saved and returned as given; nothing is
refused for this.

## Simulation

Only a rig whose links are all `sim_*`/`fake_*`; every route but the first answers 409 otherwise.

| | | |
| --- | --- | --- |
| `GET` | `/api/sim` | `{simulated, device, name, path, clock: {speed, measured, stepped, now_ns}, plants: {name: {config, links, live, readings, stats, input, output}}, changed}`; `inputs`/`outputs` for a multi-port plant; `device` says whether `/api/sim/device` exists (an application's own simulation device, e.g. a `disturb`), so a client need not probe it and 404 on a rig without one |
| `PUT` | `/api/sim/clock` | `{speed}`; the rig's time runs at `speed`× from now on |
| `POST` | `/api/sim/clock/step` | `{seconds}`; a stepped clock only |
| `GET` | `/api/sim/plants/{name}` | a plant's config and state |
| `PUT` | `/api/sim/plants/{name}` | some of its parameters, changed live; 422 for `kind` |
| `POST` | `/api/sim/plants/{name}/reset` | `{output?, input?}` |
| `GET` | `/api/sim/config` | the rig file as it now stands |
| `POST` | `/api/sim/save` | `{path?}`; writes it, default where it was loaded from |

`GET /api/clock` carries `speed` too, so a client can label a time axis.

## Events

| | | |
| --- | --- | --- |
| `GET` | `/api/events?limit=&level=` | the last few hundred `Event`s, oldest first; `level` keeps that level and above |

An `Event` is `{time_ns, level, scope, subject, kind, message, details}`;
`level` is `DEBUG`, `INFO`, `WARNING` or `ERROR`.

## Websockets

| socket | on connect | then |
| --- | --- | --- |
| `/ws/samples` | — | every sample as a `SampleOut`, as published; oldest dropped if the client lags |
| `/ws/loops` | every loop | every 50 ms, `{loops: [LoopOut]}` of those that ticked |
| `/ws/actuators` | every actuator | `{actuators: [{name, state}]}` of those that changed |
| `/ws/readers` | every reader | `{readers: [{name, ...ReaderRun}]}` as each reads, fails or restarts |
| `/ws/signals` | every registered signal | `{signals: [SignalState]}` as each registers or settles |
| `/ws/events` | the recent events | `{events: [Event]}` as each happens |

An empty flush sends nothing.
