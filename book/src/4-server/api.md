# HTTP and websocket API

All routes are under `/api`; websockets under `/ws`. Bodies and responses
are JSON. OpenAPI is `/openapi.json`, and `/docs` shows it in Swagger UI --
bundled with the runner (npm `swagger-ui-dist`, Apache-2.0), so the page
loads nothing from another host and works with no internet. There is no
`/redoc`.

Everything on the wire is named by **address**: a signal's
(`furnace.zone1`), a namespace's (`hum_sensors.dry`), a device's
(`furnace`), or a controller's, which is the address of the writable
signal it drives (`heaters.heater1`). Addresses are dotted paths with no
slashes, so they sit in one path segment.

## Authentication

Every request comes through a door: the [front](../1-running/runner/access.md)
`flyball run` or `flyballd` starts, or a bare runner's own. Everything
under `/api`, `/ws` and `/mcp` needs a verb, which the runner's verb table
names per route: `read` for a `GET`, a stream, `POST /api/rig/check` and
`POST /api/programs/check`; `operate` for everything else. The verbs are a
placeholder until D-034 (pending) is decided. `/api/auth` and its
sub-routes need nothing, and neither does a `GET` of the bundled UI on a
bare runner, nor `/docs` and `/openapi.json` there.

A caller is one of, in this order: a bearer token (`Authorization: Bearer
T`: a named token `fbt1_…` at a front, the runner's token at a bare
runner); the session cookie a sign-in set; at a `proxy` front, the identity
the proxy asserts; otherwise anonymous, who gets what `anonymous` says
(`none`, or `read`). At the `local` shape, or a bare runner with no token,
everyone gets every verb. A credential that is presented and wrong is
refused, never taken as anonymous, and a session cookie beside it does not
save the request; only at a `proxy` front is an `Authorization` that is not
a named token left to the proxy (a signed preset sends its own). A token in the URL (`?token=`) is never
read: a bare runner refuses the request, a front ignores it.

| refusal | HTTP | websocket |
| --- | --- | --- |
| no credential where one is needed, or a wrong one | `401` `{detail}`, `WWW-Authenticate: Bearer` | the handshake completes, then closed with `4401`; the UI stops retrying |
| a caller lacking the route's verb | `403` `{detail, needed}` (`"needed": "operate"`); anonymous: `401` instead, so the UI offers sign-in | closed with `4403` (anonymous: `4401`) |
| a path no row of the verb table covers (an unknown `/api/…`) | `403` `{detail, needed: null}`, never `404`; a known path with the wrong method is `405` | `403` |
| the front and the runner out of step (the runner refused the front's principal) | `502` | closed with `1014` |
| the runner still starting (not listening yet, or not answering the front's readiness probe within 2 s) | `503`, `Retry-After: 1` | `503` |
| a front already holding 512 sockets and event streams on the rig, or 128 open requests of any kind from callers with no credential | an event stream (`GET` with `Accept: text/event-stream`), or any request from a caller with no credential that cannot `operate`: `429`, `Retry-After`; any other request from a caller with a credential (the stop among them) is not counted | closed with `1013` (try again later); the UI retries |
| a request body that has not arrived two minutes after the request | `408`, and the connection is closed | -- |
| a front whose store or identity provider cannot answer | `503` | `503` |

Before any of that, a front refuses a path with a `.` or `..` segment, a
backslash or an encoded `.`, `/` or `\` (`400`); a `Host` it does not
answer to (`403`) -- under `--insecure-open` only an IP address, a loopback
name, the machine's own (`hostname`, `<hostname>.local`) or `url`'s host, on
every route, and at a `password` or `proxy` front an anonymous caller is
held to the same names on the rig's `/api`, `/ws` and `/mcp` (a websocket
is closed `4401`, the UI's "sign in"; `GET /api/auth` by another name
answers `verbs: []` and `anonymous: "none"`, so the UI offers sign-in); and a request that acts -- any method but `GET`, `HEAD`
and `OPTIONS`, and every websocket -- whose `Origin` is missing, `null` or
another site's, unless it carries a named token (`403`). A bare runner
refuses the same `Origin`s (a missing one passes there), and, with no
token, any `Host` but a loopback name -- under `--insecure-open`, any `Host`
but an IP address, a loopback name or the machine's own (`hostname`,
`<hostname>.local`), on every route. With a token, an anonymous caller is
held to those names on every route that needs a verb (`403`); `/api/auth`
answers any name. [Access](../1-running/runner/access.md#the-bare-runner)
has the rules.

| | route | |
| --- | --- | --- |
| `GET` | `/api/auth` | **AuthInfo v2**: `{v: 2, shape, scheme, user, verbs, anonymous, login, exposure, rig?}`. `shape` is the door's: `local`, `password` or `proxy` at a front; `local` (no token) or `bare` at a bare runner. `scheme` is how this caller got in: `local`, `anonymous`, `session`, `token` or `proxy`. `user` is `{id, name, kind}` (`id` the principal's `sub`, `kind` `human`, `service` or `agent`), `null` when anonymous. `verbs` are the caller's verbs on this rig. `login` is `{password, token, passkey, sso}`: what the sign-in page may offer (`passkey` is always `false`, `sso` `null`, in this release). `exposure`, when there is anything to say, is `{requested, host, port, open, restricted, open_network, warning}`: where the door serves against where it was asked to, and why (`warning` carries a front's fallback reason; after a `password` or `proxy` front's fallback, `requested` answers `503` and `host`/`port` are the fresh loopback address the `local` shape is served on). `rig` is the rig this path routes to, at a front (absent at `flyballd`'s own root). A stale cookie here answers anonymous and clears it, rather than `401` |
| `POST` | `/api/auth/login` | a `password` front: `{"password": "…"}`; a bare runner with a token: `{"token": "…"}`. Sets the session cookie (`HttpOnly; SameSite=Lax`) and answers as `GET`. Wrong: `401` after half a second; ten wrong in a minute from one address: `429` with `Retry-After` (at a bare runner ten *different* wrong tokens, or a hundred wrong attempts; a wrong `Authorization: Bearer` counts too, and while they stand every bearer request from that address is `429` too); at a front also `429` with `Retry-After: 1` while two other passwords are being checked. A front without the password shape answers `404`. Needs a same-site `Origin` at a front |
| `POST` | `/api/auth/logout` | ends the session (its open streams and sockets closed within a second) and clears the cookie |
| `GET` | `/api/auth/tokens` | a front's named tokens, `[{id, name, scopes, kind, created, expires, last_used}]`, never a secret. The admin session or the `local` shape only: anonymous `401`, anyone else `403` |
| `POST` | `/api/auth/tokens` | `{name, scopes?, kind?, expires_in?}` (`scopes` default `["read"]`, taken as given: `operate` alone holds no `read`, so ask for `["read", "operate"]` to drive and watch; `kind` default `service`, `expires_in` seconds, capped as [the lifetimes](../7-reference/rig-file.md#token-lifetimes) say) → `201` `{token, id, name, scopes, kind, created, expires, last_used}`; `token` is shown this once. `manage` is refused (`403`): only `flyball token create` issues it. The same callers as `GET`; a record the front cannot write in its audit means no token (`503`) |
| `DELETE` | `/api/auth/tokens/{id}` | `204`; the token's open streams and sockets closed within a second; `404` no such token. The same callers. A revoke the front cannot record in its audit still happens (`503`, saying so) |
| `GET` | `/api/auth/link?n=NONCE` | a bare runner with a token: a one-time sign-in link (printed at start; ten minutes). `302` to `<root>/` with a session cookie, `Referrer-Policy: no-referrer`; used, expired or wrong: `401` |
| `POST` | `/api/auth/link` | a bare runner, with its token as a bearer: `{url, expires_in}`, a fresh link for a person |
| `GET` | `/api/auth/front` | a fronted runner's readiness probe: `401` without a valid principal, `200` `{protocol: 1, aud, pid, flyball}` with one; `404` on a bare runner |

A bare runner's session cookie is `flyball-bare-<port>`, path `<root
path>/`, in memory for 12 hours; a front's is `flyball-<port>`, or
`__Host-flyball` under HTTPS, path `/`, and ends after 12 idle hours or 7
days. The cookie's value is opaque to a client.

Started with `--root-path /p`, every path below sits under `/p`
(`/p/api/health`, `/p/ws/samples`, `/p/mcp/read`); anything not under it
is `404` (a socket is closed with 4404). Under `flyballd`, each rig's paths
sit under its `root_path` the same way.

## Stopping the rig

| | | |
| --- | --- | --- |
| `POST` | `/api/rig/stop` | the [software stop](../1-running/runner/access.md#stopping-the-rig). Body optional: `{"reason": "…"}` (cut to 500 characters). Needs `operate`; never rate-limited, and it runs on threads of its own. `503` with no rig. Answers the report: `{at_ns, actor: {sub, sid, kind, via, detail}, reason, devices: {name: {state, detail}}, program_interrupted, controllers_manual, interim}` -- `via` is `http`, `mcp` or `signal`; `state` is `stopped`, `unchanged` or `failed` for each device with a writable signal; `controllers_manual` names every controller in manual afterwards. In this release `interim` is `true` and nothing is written, so each device is `unchanged` |

## The daemon's own routes

`flyballd` answers these at its root, not under a rig's `root_path`.

| | | |
| --- | --- | --- |
| `GET` | `/api/rigs` | the rigs the caller holds any verb on: `[{name, root_path, status}]`, sorted by name. Needs no credential: an anonymous caller gets the rigs `anonymous` lets it see (`[]` under `anonymous: none`), and only by an IP address, a loopback name, the machine's own name or `url`'s host (`403` by another name: DNS rebinding); a credential that is presented and wrong is `401`. `flyball stop --all` uses it |
| `GET` | `/api/runners` | every registered runner: `[{name, root_path, restart, status, endpoint, pid, adopted, reason}]` |
| `GET` | `/api/runners/{name}` | one of them |
| `POST` | `/api/runners` | a manifest as JSON: register and start it, `202`; `400` a bad manifest, `409` a name or root path taken |
| `DELETE` | `/api/runners/{name}` | stop the runner process and deregister it, `204` |
| `POST` | `/api/runners/{name}/restart` | `204` |
| `GET` | `/api/runners/{name}/logs` | its captured output, as plain text |
| `GET` | `/` | the landing page: a link to each rig |

All but `/api/rigs` are management: a named token with the `manage` scope
as a bearer; `401` without a credential, `403` with any other (a session
never has it). Statuses and what each field means: [the
daemon](../7-reference/cli.md#a-runners-status).

## The runner

The process itself, apart from the rig it serves. `404` under a server
that is not `flyball-runner` (a test client, an application's own `serve`
without a handle).

| | | |
| --- | --- | --- |
| `GET` | `/api/runner` | `{endpoint, root_path, mcp, compose, allow_save, allow_shutdown, store, programs, tunings, drivers, files, keep, keep_size, retain, rotate, max_store, keep_ns, keep_bytes, retain_ns, rotate_ns, max_bytes}`: the settings as resolved (the `runner:` section under the command line), never the token; `endpoint` is what the runner binds, `tcp:<host>:<port>`, or `unix:<path>` behind a front; `files` the rig files loaded; the five retention keys as written and as resolved (0 = off / no cap) |
| `POST` | `/api/runner/shutdown` | 202 `{detail}`; the rig stops and the process exits. 409 unless started with `--allow-shutdown`. Not the [software stop](#stopping-the-rig) |
| `POST` | `/api/runner/restart` | 202 `{detail}`; as shutdown, then the same command line runs again in the same process. 409 the same |

## Errors

`{"detail": "<message>"}` with the status from the error's base:
404 `NotFoundError` (an address, a device, a command, a tuning), 409
`ConflictError` (a demand the rig refuses, a signal already spoken for,
a unit mismatch), 422 `UnachievableError` or `ValueError`, 503
`NotReadyError` (no rig, nothing read yet, no default controller) or
`HardwareError`; 401, 403 and 429 from the door, and 502 and 503 from a
front ([authentication](#authentication)).

The store's own failures take the same map. A write it refuses -- a tuning
whose `session_id` names no session, a duplicate of a unique row -- is 409
`ConstraintError`; a store it cannot reach -- locked by another writer, out
of disk -- is 503 `StoreUnavailableError`. Both carry sqlite's message in
`detail`. Anything else from the store is a bug and answers 500, never 503,
so a 500 is not worth retrying. A 503 usually is, though sqlite files a
transaction begun inside another under the same error; `detail` says which.

## Rig

| | | |
| --- | --- | --- |
| `GET` | `/api/health` | `{ok, rig, uptime_s, devices, controllers, conditions, alarms, activities, recording}`; `devices` is `{name: {running, last_read_ns}}` for each polled device, `controllers` is `{name: mode}`; `conditions` is every device's own (`[{device, kind, level, message, since_ns}]`), then the runtime's (`offline`, `slow` from polling, `write_failed` from a blocking writer, `commit_failed` from a commit on the delivery path); `alarms` is `{warn, alarm, max_level}`: the latest reading on every signal, those outside their `warning` band (amber) or `alarm` band (red, not double-counted as warn) -- a reading that is not a finite number (`null`, NaN, an infinity, a string) counts as neither --, plus conditions at `WARNING` (30, counts as warn) or `ERROR` (40, counts as alarm); `max_level` is `40`/`30`/`0`; `exposure` is the runner's own, as in a bare runner's `GET /api/auth` (behind a front: `fronted: true`, its socket's `endpoint`, and `notes` on the settings it ignores); `{ok: false, rig: null, exposure}` with no rig |
| `GET` | `/api/schema` | `{devices: {name: DeviceSchema}}` |
| `GET` | `/api/clock` | `ClockOut`: `{start_time_ns, now_ns, elapsed_ns, tags, speed}` |
| `GET` | `/api/tunings` | `{name: LawConfig}` |
| `GET` | `/api/tunings/{name}` | `LawConfig` |
| `PUT` | `/api/tunings/{name}` | body `LawConfig`; replaces the tuning on the live rig |

A `LawConfig` is `{type, ...gains}`, e.g. `{"type": "PI", "kp": 0.5, "ki": 0.05, "tt": 0}`.

### Composition

The rig built up while it runs, in the rig file's own terms; every change
is a version in the store (see [the runner](../1-running/runner/building.md#building-a-rig-while-it-runs)).
On a rig with real hardware links the writes below (links, devices, a
document, restore) answer `409` unless the runner runs with `--compose`;
a simulated rig, or one started bare, may always be built up. Reading and
saving are never gated.

| | | |
| --- | --- | --- |
| `GET` | `/api/rig/schema` | the rig file's JSON schema, with every driver and link type this runner has |
| `GET` | `/api/rig/config` | the rig file as loaded (a simulation's, with its changes); of `runner:` only what a reader needs -- `host`, `port`, `log_level`, `compose`, `mcp`, `root_path`, `allow_save`, `allow_shutdown`, the retention keys, `auth.anonymous`, and `front`'s `listen`, `auth`, `url` and `anonymous`. No credential (`auth.token`, `front.password`), no path (`store`, `drivers`, `front.tls`, ...), none of `front.proxy` or `front.trusted_proxies` |
| `POST` | `/api/rig/check` | body a rig document; validates without building; 422 says what is wrong |
| `POST` | `/api/links` | body `{name, type, ...}` (a `links:` entry with its name); 201 the link as the file writes it; 409 the name is taken; 422 a bad config |
| `DELETE` | `/api/links/{name}` | 204; 409 while a device is built on it |
| `POST` | `/api/devices` | body the file's device envelope with its `name` (`driver`, `label`, `poll_s`, `signals`, `inputs`, and the driver's fields flat beside them); 201 `DeviceOut`, bound, polled and recorded; 409 name taken; 404 unknown link or input address; 422 unknown driver or a config it refuses |
| `DELETE` | `/api/devices/{name}` | 204; its poll stops, controllers on it are detached, inputs bound into it unbound |
| `POST` | `/api/rig` | body a rig document (`links`, `devices`, `controllers`; other keys ignored); added in that order; 201 the running document |
| `GET` | `/api/rig/document` | the running rig as a rig file would build it, defaults left out |
| `GET` | `/api/rig/changes` | what differs from the rig as this run started, as an overlay (a removed key is `null`); `{}` when nothing |
| `GET` | `/api/rig/versions` | `[{id, time_ns, reason, files, parent, head}]`, newest first; `?limit=`. `parent`: the version this was made from (the head when it was saved; `null` for a first); `head`: whether the running rig is at it |
| `GET` | `/api/rig/versions/{id}` | the same with `document` |
| `POST` | `/api/rig/versions/{id}/restore` | make the running rig that version: links, devices and controllers removed, added or rebuilt to match. Writes no version: the head moves to `{id}`, and the next change's `parent` is `{id}`; a `rig`/`versions`/`restored` event marks it on the event stream |
| `GET` | `/api/drivers` | every registered type: `{role: "driver" \| "link", module, description, schema}` (`schema_error` in place of `schema` if pydantic cannot build one) |
| `POST` | `/api/drivers/reload` | re-import the runner's drivers directory (`--drivers`, default `drivers/` beside the first rig file): `{directory, registered: {file: [tags]}, errors: {file: message}}`; a file's earlier tags are dropped first, so an edited driver re-registers; 404 with no directory |
| `POST` | `/api/probe` | `{report}`: the board's buses, GPIO chips and I²C addresses (flyball-linux); `?scan=false` for the list without a bus transaction; 404 where it is not installed. A `POST` because a scan drives every I²C bus |
| `POST` | `/api/links/{name}/query` | body `{text}`; `{reply}` from a text link's `query()`; 409 for a link that is not one |
| `POST` | `/api/rig/save` | body `{path?, overwrite?}`; no path: the changes to `<rig>.d/added.<suffix>` beside the first rig file (409 if the runner was not started from a file); a path: the whole rig, flattened (409 unless the runner runs with `--allow-save`; 422 a bad suffix; 409 a file the rig was loaded from unless `overwrite`; an existing file keeps its own `runner:` section, which is not returned; 409 if that section cannot be read); returns `{path, document}` |

## Devices

Every device has one name rig-wide, whatever its driver; `/api/devices`
lists them all with their signal trees.

| | | |
| --- | --- | --- |
| `GET` | `/api/devices` | `[DeviceOut]` |
| `GET` | `/api/devices/{name}` | `DeviceOut`; 404 if no device has that name |
| `GET` | `/api/devices/{name}/schema` | the `DeviceSchema` |
| `POST` | `/api/devices/{name}/commands/{command}` | body: the command's arguments; returns what the method returns; 503 when a linked argument's demand has a limit not known yet (not run); a command that succeeds on an offline device restarts its polling |
| `POST` | `/api/devices/{name}/restart` | poll an offline device again on its period; `DeviceOut` |
| `PUT` | `/api/devices/{name}/write` | body `{name: value, ...}`, names relative to the device (dotted under a namespace: `position.x`), values in each signal's unit; one atomic write, committed at once; returns `{address: WriteOut}` for each signal set; 409 for a signal a controller drives, or a signal that is not writable; 503 `LimitNotKnownError` while a signal's limit follows another signal that has no value yet, or a non-finite one (NaN, inf) -- refused whole, never passed unclamped; 404 for a name not under the device |
| `PUT` | `/api/signals/{address}` | body a number: the single-signal write; returns `{address: WriteOut}`; 409 if the address is a namespace; 503 while its limit is not known yet, as above |

A `DeviceOut` is `{name, label, kind, driver, class_name, link, poll_s, signals,
commands, inputs, readable, writable, conditions, run}`: `kind` is
`device`, or `simulation` for an application's own simulation device (see
[Simulation](#simulation)), `driver` the rig file's `driver:` (null for a device
built in code), `class_name` its Python class, `link` the rig file's name for the link
it was built on (or null), `signals` the tree, `commands` `[CommandOut]`,
`inputs` `{role: InputOut}` — what the device follows, and what is bound to
it — `readable`/`writable` whether it implements `read`/`commit`,
`conditions` its own (pushed onto its `conditions` output) plus the
runtime's (`offline`, `slow`), and `run` `{period_s, running,
last_read_ns}` for a polled device (null otherwise).

A signal in the tree is `{name, address, access, role, tags, label,
quantity, unit, dimension, dtype, shape, range, precision, warning, alarm,
poll_s, limits, initial, latest, write}`: `access` is the set in force as
letters (`rp`, `w`, `rw`, `rpw`), `role` one of `demand`, `readout`,
`setting`, `config`, `tags` the section as `{axis: name}` (empty without
one), `limits` the numbers in force now, `latest` `{time_ns, value}` once
it has been read (null before), `write` a `WriteOut` for a writable signal
once it has been set. A namespace is `{name, address, atomic, label,
poll_s, signals: [...]}`, nesting the same shapes.

A `WriteOut` is `{value, requested, at_limit, controller}`: what was last
set after limits, what was asked for when the clamp changed it, `low` /
`high` when the value sits on a limit, and the controller driving the
signal (it refuses manual demands; set its setpoint or detach it).

A `CommandOut` is `{name, description, simulation, commit, mode,
interrupts, demand_of, links}`: `commit` whether the rig commits the
device once the method returns, `mode` what the device's `mode` output
becomes when it runs (if it has one), `interrupts` whether it may put a
controller into manual and run anyway, `demand_of` the path of the demand
it sets for a synthesised `set_<name>`, and `links` `{argument: demand
path}` for every argument that is a value for a demand.

`GET /api/devices/{name}/schema` returns `{name, label, class_name, driver,
description, readable, writable, config, signals, inputs, commands}`:
`config` a JSON Schema for the driver's config, `signals` `{path: {address,
access, role, tags, label, quantity, unit, dimension, dtype, value, range,
precision, limits}}` by path relative to the device (`value` a JSON Schema
for the signal's own type), `inputs` `{role: {label, quantity, unit,
bound}}`, and `commands` `{command: {description, arguments, simulation,
commit, mode, interrupts, demand_of}}` — `arguments` a JSON Schema whose
properties linked to a demand also carry `x-signal`, `unit` and
`minimum`/`maximum` from that signal's limits now.

## Reading

| | | |
| --- | --- | --- |
| `GET` | `/api/read/{address}?fresh=` | what the address names: a signal → `{reading: {signal, time_ns, value}}`; an atomic namespace → `{sample: {node, time_ns, values}}`, `values` keyed relative to the node; a device or a namespace read over several transactions → `{samples: [...]}`; `fresh=true` reads the hardware first, which is how a setting (`rw`, never published) is read; 503 until the first read, 404 for an unknown address |
| `GET` | `/api/read?at=a,b,c&fresh=` | several addresses at once, a list in the order given; a fresh read costs each device one read |

## Controllers

A controller regulates one published signal, its **measured** signal,
by writing one demand, its **output**, through a law and a feedforward. It
is named by its output's address.

| | | |
| --- | --- | --- |
| `GET` | `/api/controllers` | `[ControllerOut]` |
| `GET` | `/api/controllers/default` | `ControllerOut`; 503 when there is none |
| `GET` | `/api/controllers/{address}` | `ControllerOut` |
| `GET` | `/api/controllers/schema` | what a form needs to make a controller: `measured` and `outputs` (`[{address, device, label, unit, dimension, range, limits}]`: every published signal, every writable one), `laws`, `feedforwards` and `generators` (JSON Schema unions on `type`), `tunings` (`{name, law, config}`), `regulated` (`{measured: controller}`), `driven` (`{output: controller}`) |
| `POST` | `/api/controllers` | `{output, measured, law?, feedforward?, default?, min_period_s?}` (the rig file's keys); 201 `ControllerOut`; 409 if the output is already driven or the measured signal already regulated, or `feedforward: "setpoint"` across units; 404 for an unknown address |
| `DELETE` | `/api/controllers/{address}` | 204; put in manual first, so the output holds its last value; manual demands may drive it again |
| `POST` | `/api/controllers/{address}/regulate` | `{at, start?, tuning?, transfer?}`; `at` a value, `measured`/`setpoint`/`output`, or a generator spec (`{type, ...its own arguments}`, e.g. `{type: "linear_ramp_setpoint", pace, end}`, discriminated by `type` against the `generators` union); `start` says where a generator starts from -- a value, `setpoint` or `measured` (the last reading) -- and defaults to the controller's current setpoint, or its last reading if it has none yet; `output` is converted back to the measured unit through the feedforward's inverse, 422 if it has none; the handover's output is committed at once; 503 if a generator is given and there is neither a setpoint nor a reading to start it from |
| `POST` | `/api/controllers/{address}/manual` | stop regulating; the output keeps its last value |
| `PUT` | `/api/controllers/{address}/setpoint` | `{at, start?}`; move the setpoint, or start following a generator spec (as `regulate` takes, with the same `start`), without touching the mode |

A `ControllerOut` is `{name, label, output_signal, measured_signal,
default, mode, law, feedforward, output_unit, reference, setpoint, arrived,
correction, output, expected, delivered_correction, measured}`: `name` is
`output_signal`, the output's address, and `measured_signal` the measured
signal's; `label` the output signal's; `reference` is a number or, mid-trajectory, `{type,
...the generator's own arguments, end_time?}` (`end_time` in seconds from
the rig's start, once started and unless endless), `setpoint` the value it
resolved to at the last tick (in the measured unit), `arrived` whether the
reference has landed (a number has; a generator once it finishes, judged
in rig time), and `output`, `expected` and `correction` are in
`output_unit` -- the output signal's unit, which the `feedforward` (`{type:
setpoint | none | affine | table, ...}`, `affine`/`table` taking an
optional `rate_gain` for a ramp's rate of change) maps the setpoint into;
`measured` is the measured signal's reading at the last tick, `{signal,
time_ns, value}`. The faceplate reads Measured / Setpoint / Output from
`measured`, `setpoint` and `output`.

The generators, by `type`:

| type | arguments | on the wire once started |
|---|---|---|
| `linear_ramp_setpoint` | `pace` (a rate, `{per_minute: 10}`, or a duration for the whole walk), `end` | `end_time`; starts from the current setpoint or reading and walks to `end`, so a ramp to where it already is finishes at once; a descending ramp's rate is negative |
| `dwell` | `value`, `duration?` | `end_time` when it has a duration; without one it never finishes |
| `profile` | `segments`: a list of generator specs (this union, recursively) | `segments` as given, `active` (the index of the segment in force at the last tick), `end_time` unless the last segment is endless; each segment starts where the previous landed (a ramp's `end`, a dwell's `value`), the first from the profile's own start; 422 if a segment before the last never ends, or there are none |

## Activities

What the rig is waiting on: a program step's prompt, a settle test, a
timed wait, a ramp's end.

| | | |
| --- | --- | --- |
| `GET` | `/api/activities` | `{name: ActivityOut}` |
| `GET` | `/api/activities/{name}` | `ActivityOut` |
| `POST` | `/api/activities/{name}/fire` | `{name, fired: bool}`; false if already settled |
| `POST` | `/api/activities/{name}/cancel` | `{name, cancelled: bool}`; the program ends `cancelled` |

An `ActivityOut` is `{name, message, outcome, since_ns, timeout_s, prompt}`
with `prompt` true only for a `prompt` step (an activity only a person
answers), false for a timed `wait`, a `settle` or a ramp that settles by
itself, and `outcome` one of `pending`, `fired`, `timeout`, `interrupted`.

## Recording

The one place the rig and the store meet: opening a session needs both.

| | | |
| --- | --- | --- |
| `GET` | `/api/recording` | the open *named* session as a `SessionRow`, or `null` -- `null` while only the [scratch record](../1-running/runner/index.md#the-scratch-record) runs |
| `POST` | `/api/recording` | `{details?, version?, config?, hardware?, include_ns?}`; 201 `SessionRow`. `include_ns` starts the session that far back (clamped to what scratch holds) and backfills it from the scratch record it replaces. 409 if one is open |
| `POST` | `/api/recording/end` | close it; `SessionRow`. The scratch record reopens |

## History

Reads the store, never the rig.

| | | |
| --- | --- | --- |
| `GET` | `/api/history/sessions?limit=&kind=` | `[SessionRow]`, newest first; `kind=session` or `scratch` filters, unfiltered lists both |
| `GET` | `/api/history/sessions/{id}` | `SessionRow` |
| `PUT`/`PATCH` | `/api/history/sessions/{id}` | `{pinned?: bool, name?: str}`, either or both; `SessionRow`. Pinned is exempt from `retain` and `max_store`; `name` sets or (given `null`) clears `details.name`, keeping every other `details` key untouched |
| `POST` | `/api/history/sessions/{id}/keep` | `{start_ns, end_ns, details}` (absolute ns in the rig's clock); 201 `SessionRow`, closed: declarations copied whole, readings / write states / ticks / events in `[start, end)` rebased to the new start, spans not copied. 422 when empty, before what the session holds, after its end, or in the future |
| `POST` | `/api/history/sessions/{id}/end` | close a session; 409 on the live scratch record |
| `DELETE` | `/api/history/sessions/{id}` | 204; everything the session recorded goes; tunings survive. 409 on the live scratch record or the session being recorded |
| `GET` | `/api/history/sessions/{id}/devices` | `[DeviceRow {id, address, driver, config, label}]` |
| `GET` | `/api/history/sessions/{id}/signals` | `[SignalRow {id, device_id, address, quantity, unit, access, dtype, shape, label, range, precision, warning, alarm, limits}]` |
| `GET` | `/api/history/sessions/{id}/writes` | `[WriteRow {signal: SignalRow, driver, limits}]` |
| `GET` | `/api/history/sessions/{id}/controllers` | `[ControllerRow {name, measured, law, feedforward}]` |
| `GET` | `/api/history/sessions/{id}/series/{address}` | `Series {signal: SignalRow, points, downsample}`; query `start_ns`, `end_ns`, and one of `every`, `bucket_ns`, `max_points` |
| `GET` | `/api/history/sessions/{id}/writes/{address}` | `[WriteStateRow {offset_ns, value, requested, at_limit, controller}]`; query `start_ns`, `end_ns` |
| `GET` | `/api/history/sessions/{id}/ticks/{controller}` | `[Tick {controller, offset_ns, mode, correction, measured, setpoint, output, expected, delivered_correction}]`; query `start_ns`, `end_ns`. A tick's `correction` is `null` when the law's output was not a number (a NaN integral) |
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

A program is a document in the server's [dialect](../1-running/programs/writing.md);
`check` and `run` take it as the body. Steps name a controller by its
output's address (or none for the rig's default), a device by name.

| | | |
| --- | --- | --- |
| `GET` | `/api/programs/schema` | JSON Schema for a program file in the dialect |
| `GET` | `/api/programs/commands` | the internally tagged request union as JSON Schema |
| `POST` | `/api/programs/check` | normalise and validate a document; 422 names the step that fails to parse; else `ProgramCheck {ok, error?, normalised, warnings}` |
| `POST` | `/api/programs/run?cancel=` | start a document (`cancel` cancels one already running); returns `ProgrammerState` once the first step is applied |
| `POST` | `/api/programs/command?cancel=` | one internally tagged command |
| `GET` | `/api/programs/running` | `ProgrammerState` |
| `POST` | `/api/programs/cancel` | cancel whatever is running: it ends `cancelled`, outputs kept |
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
`dashboards/*.toml`, `*.yaml` or `*.json` beside its file (any format a rig
file itself takes); they are imported on start.

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

Documents carry `schema_version: 3`, which adds two fields to version 2:
`readonly` (bool, default `false`: the app disables the dashboard's write
controls for everyone; a convenience, not access control) and `order`
(a number or `null`, default `null`: where its tab sits, ascending, with
unordered dashboards after, newest saved first). An older document is
migrated on read, never refused, and what is stored stays as saved. A
version-2 document reads as writable and unordered. A version-1 document
(bindings to channels, loops and actuators) is migrated too: `channel` (`"source.measurand"` or
`{source, measurand}`) becomes `address`, `channels` become `addresses`,
a `loop` widget's `loop` becomes `controller`, and an `actuator` widget
becomes a `device` widget bound by `device`. A loop was named by its
actuator and a controller by its output's address, so a migrated `loop`
binding may show as a problem until it is rebound.

## Simulation

Only a rig whose links are all `sim_*`/`fake_*`; every route but the first answers 409 otherwise.

| | | |
| --- | --- | --- |
| `GET` | `/api/sim` | `{simulated, device, name, path, clock: {speed, measured, stepped, now_ns}, plants: {name: {config, links, live, readings, stats, input, output}}, changed}`; `inputs`/`outputs` for a multi-port plant; `readings` keyed by signal address; `device` says whether `/api/sim/device` exists (an application's own simulation device, e.g. a `disturb`), so a client need not probe it and 404 on a rig without one |
| `PUT` | `/api/sim/clock` | `{speed}`; the rig's time runs at `speed`× from now on; 409 on a stepped clock |
| `POST` | `/api/sim/clock/advance` | `{seconds}`; a stepped clock only |
| `GET` | `/api/sim/plants/{name}` | a plant's config and state |
| `PUT` | `/api/sim/plants/{name}` | some of its parameters, changed live |
| `POST` | `/api/sim/plants/{name}/reset` | `{output?, input?}` |
| `GET` | `/api/sim/config` | the rig file as it now stands; `runner:` cut down as for `/api/rig/config` |
| `POST` | `/api/sim/save` | `{path?}`; writes it, default where it was loaded from; 409 unless the runner runs with `--allow-save` |
| `GET` | `/api/sim/device` | the application's simulation device: `{config, values}` (`values` its signals' current readings, by path); 404 without one |
| `GET` | `/api/sim/device/schema` | its `DeviceSchema` |
| `POST` | `/api/sim/device/{command}` | one of its commands |

`GET /api/clock` carries `speed` too, so a client can label a time axis.

## Events

| | | |
| --- | --- | --- |
| `GET` | `/api/events?limit=&level=` | the last few hundred `Event`s, oldest first; `level` keeps that level and above |

An `Event` is `{time_ns, level, scope, subject, kind, message, details}`;
`level` is `DEBUG`, `INFO`, `WARNING` or `ERROR`. `scope` is `device`,
`controller`, `program` or `rig`, and `subject` names which one. `kind` is
one of a fixed set:

| scope | kinds |
| --- | --- |
| `device` | `offline`, `restarted`, `slow`, `write_failed`, `write_recovered`, `commit_failed`, `commit_recovered`, `demand_ignored` |
| `controller` | `step_failed`, `step_recovered`, `stale_input`, `limit_unknown`, `limit_known`, `interrupted` |
| `program` | `started`, `step`, `step_timed_out`, `step_failed`, `succeeded`, `failed`, `cancelled` (a person), `interrupted` (the engine, with `details.reason`), `run_from_library` |
| `rig` | `delivery_failed`, `recording_failed`, `restored` |

A [`Condition`](wire.md#devices) the runtime raises uses the same kinds
(`offline`, `slow`, `write_failed`, `commit_failed`); a driver's own conditions may use any
string.

`commit_failed` (`ERROR`) is a device's `commit` that raised on the delivery
path: once per outage, with the demands it dropped in `details.signals`, and
a `commit_failed` condition on the device until a commit succeeds
(`commit_recovered`, `INFO`). The dropped demands are not sent later; the
controller driving one hears `expected: null` for that tick, and the rest
of the delivery -- other devices' commits, the recorder -- goes on. A
manual demand or a command whose commit raises also gets the error back.
`write_failed` / `write_recovered` are the same for a blocking device's
writer thread; a write that reached the device but whose report to the rig
raised is a `write_failed` too (logged; the thread goes on writing).

`demand_ignored` (`WARNING`) is a demand the driver's `commit` never read
(`details: {signal, demand}`): nothing was set, so the demand is not
echoed as the signal's reading. Its write record keeps the reading as it
was and carries the demand as `requested`. Once per signal until a demand
on it is read again.

## Websockets

Every socket sends what the rig knows on connect, then every 50 ms one
frame of whatever changed: the rig keeps only the newest value per key,
so a socket costs at most one frame per flush at any tick rate. An empty
flush sends nothing.

| socket | on connect | then |
| --- | --- | --- |
| `/ws/samples` | the newest published sample per node, and every polled device's run | `{samples?: [SampleOut], runs?: [{name, period_s, running, last_read_ns, conditions}]}`, either key present only when something in it changed; at most one sample per node, and one run per device, per flush |
| `/ws/controllers` | every controller | `{controllers: [ControllerOut]}` of those that ticked |
| `/ws/activities` | every registered activity | `{activities: [ActivityOut]}` as each registers or settles |
| `/ws/events` | the recent events | `{events: [Event]}` as each happens |

A `SampleOut` is `{node, time_ns, values, writes}`: only published signals,
`values` keyed relative to `node`; `writes` carries `{requested, at_limit,
controller}` for each demand the sample includes, keyed the same way as
`values` -- a demand's write record rides with its reading now, so
`/ws/writes` no longer exists (`GET /api/devices`' `SignalOut.write` still
carries the last committed value too). `/ws/devices` is likewise gone: a
device's run rides beside its samples, under `runs`, on `/ws/samples`.
