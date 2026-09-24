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
| `POST` | `/api/rig/stop` | the [software stop](../1-running/runner/access.md#stopping-the-rig): the rig latched, long commands cancelled, the program interrupted, every controller to manual, then each device's [resolved stop](../2-config/devices/index.md#stop-what-a-stop-writes) written, all devices at once within 5 s. Body optional: `{"reason": "…"}` (cut to 500 characters). Needs `operate`; never rate-limited, and it runs on threads of its own. `503` with no rig. Answers the [`StopReport`](wire.md#stopping-and-latches): `{at_ns, actor: {principal, kind, via, sid, message}, reason, devices: {name: {state, message, written, kept}}, program_interrupted, controllers_manual, interim, latched}` -- `via` is `http`, `mcp` or `signal`; `state` is `stopped` (its stop command ran, or its stop values were written), `unchanged` (every output kept) or `failed` (`message` says why; "may still act" when the time ran out) for each device with demands or a stop command, never a `driver: values` one; `written` and `kept` are `{address: value}` (`null`: not known); `controllers_manual` names every controller in manual afterwards; `interim` is `false`; `latched` is `true`. A second stop latches nothing new and writes the stops again |
| `GET` | `/api/rig/stop` | what a stop would do: `{stopped, outputs}`. `stopped` is the rig stop's [`LatchOut`](wire.md#stopping-and-latches), or `null`; `outputs` is `[{address, device, stop, origin, command, controller, covered_if_flyball_dies, warnings}]` for every writable demand, sorted `off`, `you_said`, `keep`, then those a command stops -- `stop` a number, `"keep"`, or `null` when `command` (the device's stop command) runs; `origin` `off` (the driver's), `you_said` (the rig file's `stop:`), `nobody_said` or `command`; `controller` the one driving it, or `null`; `covered_if_flyball_dies` always `false`; `warnings` flag a controller's output nobody gave a stop and an unbounded `on_fault: freeze` on an output whose stop is `off`. Needs `read` |
| `POST` | `/api/rig/reset` | body optional: `{"cause": "stop"}` (the default) or `{"cause": "on_fault:<controller>"}`; lets that latch go and answers its `LatchOut`. Needs `operate` and a person: `403` for a service token or an agent through MCP; `404` when nothing holds that cause. Resumes nothing: controllers stay in manual, programs stay ended. A fault's Reset clears its controller's law state |
| `GET` | `/api/rig/latches` | every latch held: `[LatchOut]`. Needs `read` |

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
| `POST` | `/api/runner/shutdown` | 202 `{message}`; the rig stops and the process exits. 409 unless started with `--allow-shutdown`. Not the [software stop](#stopping-the-rig) |
| `POST` | `/api/runner/restart` | 202 `{message}`; as shutdown, then the same command line runs again in the same process. 409 the same. A [rig edit](#composition) restarts the runner itself and needs no `--allow-shutdown` |

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
| `GET` | `/api/health` | `{ok, rig, uptime_s, devices, controllers, conditions, alarms, activities, recording, stopped, latches}`; `stopped` is `{actor, at_ns, reason}` while the rig is latched by a stop, else `null`; `latches` is `[{subject_kind, subject, cause}]`, every subject a latch holds; `devices` is `{name: {running, last_read_ns}}` for each polled device, `controllers` is `{name: mode}`; `conditions` is every [`Condition`](wire.md#devices) held now, from the rig's condition store, on any device, signal, controller or the rig itself (`[{code, severity, message, since_ns, subject_kind, subject, details}]`: `offline`, `slow` from polling, `write_failed` from a blocking writer, `commit_failed` from a commit on the delivery path, `stale_input`/`limit_unknown`/`step_failed`/`not_permitted` on a controller, `stopped` on the rig and `latched` on what an `on_fault` action holds, `band_warning`/`band_alarm`/`band_unknown` on a signal, `frozen` on a controller, `recording_failed` on the rig, and each driver's own on its device or signals); `alarms` is `{warn, alarm, unknown, max_level}`, counted from the rig's band conditions: `warn` is the signals holding `band_warning` (outside their `warning` band), `alarm` those holding `band_alarm` (outside `alarm`), `unknown` those holding `band_unknown` (a banded signal with no value because of a fault, past its grace, whose `on_no_value` is `fire`), each signal once, `unknown` first; these keep the rig's hysteresis ([Bands](../2-config/devices/index.md#bands)). Fault conditions are never alarms: one offline device is one condition and zero alarms. `max_level` is `40`/`30`/`0` from `alarm`/`warn`; `unknown` does not raise it. `ok` is false while any condition at `error` is held other than a band condition -- a band alarm is not a fault; `exposure` is the runner's own, as in a bare runner's `GET /api/auth` (behind a front: `fronted: true`, its socket's `endpoint`, and `notes` on the settings it ignores); `{ok: false, rig: null, exposure}` with no rig |
| `GET` | `/api/schema` | `{devices: {name: DeviceSchema}}` |
| `GET` | `/api/clock` | `ClockOut`: `{start_time_ns, now_ns, elapsed_ns, speed}` |
| `GET` | `/api/tunings` | `{name: LawConfig}` |
| `GET` | `/api/tunings/{name}` | `LawConfig` |
| `PUT` | `/api/tunings/{name}` | body `LawConfig`; replaces the tuning on the live rig |

A `LawConfig` is `{type, ...gains}`, e.g. `{"type": "pi", "kp": 0.5, "ki": 0.05, "tt_s": 0}`.

### Composition

The rig changed while it runs, in the rig file's own terms (see
[the runner](../1-running/runner/building.md#building-a-rig-while-it-runs)).
A change is never applied to the running rig (D-051). Each of the six edits
below -- add or remove a link, add or remove a device, add a document,
restore a version -- goes one way:

1. **Check.** The edited rig is validated whole (the schema, the driver,
   link and law types, and that every input and controller address names a
   device of the rig). Refused with nothing changed: `422` invalid, `404` an
   unknown link or an address on no device, `409` a name taken, a link still
   used, a device another's input is bound to, a key a `--set` pins, a stale
   `?base=`, a program running without `?force=true`, a runner already
   restarting, no change at all, or an edit the overlay cannot express.
2. **Save.** A new rig version, the head, with reason `edited: <what>` or
   `restored from N`; for a rig from files, also the overlay
   `<first file>.d/added.<suffix>` the next start loads (checked first to
   rebuild exactly that version; the one it replaces is kept as
   `added.<suffix>.prev`). A bare or `--resume`d runner keeps it in the store
   alone.
3. **Stop** the rig with the installed stop, as `POST /api/rig/stop` does
   (the program cancelled, outputs to their stop states, controllers to
   manual), not latched.
4. **Restart** the runner by exec (a bare or resumed one with `--resume`):
   it builds the head version and comes up passive, every controller in
   manual and each driver at its build values; a recording goes on in a new
   session. If it cannot build, it puts the version before back (`restored
   from M`), starts again once, and holds an `edit_not_built` condition on
   the rig.

Each answers 202 `RigEditOut`: `{version, previous, reason, saved, restarting,
stop, message}` -- `saved` the overlay's path or `null`, `stop` the stop's
report -- before the restart; the API answers again once the runner is back.
Each takes `?base=<version>` (the head the edit was made on; `409` if it
moved) and `?force=true` (cancel a running program). On a rig with real
hardware links the edits answer `409` unless the runner runs with
`--compose`; a simulated rig, or one started bare, may always be changed.
Reading and saving are never gated. Attaching a controller
(`POST /api/controllers`) is still applied in place.

| | | |
| --- | --- | --- |
| `GET` | `/api/rig/schema` | the rig file's JSON schema, with every driver and link type this runner has |
| `GET` | `/api/rig/config` | the rig file as loaded (a simulation's, with its changes); of `runner:` only what a reader needs -- `host`, `port`, `log_level`, `compose`, `mcp`, `root_path`, `allow_save`, `allow_shutdown`, the retention keys, `auth.anonymous`, and `front`'s `listen`, `auth`, `url` and `anonymous`. No credential (`auth.token`, `front.password`), no path (`store`, `drivers`, `front.tls`, ...), none of `front.proxy` or `front.trusted_proxies` |
| `POST` | `/api/rig/check` | body a rig document; validates without building; 422 says what is wrong |
| `POST` | `/api/links` | body `{name, type, ...}` (a `links:` entry with its name); 202 `RigEditOut`; 409 the name is taken; 422 a bad config |
| `DELETE` | `/api/links/{name}` | 202 `RigEditOut`; 404 no such link; 409 while a device is built on it |
| `POST` | `/api/devices` | body the file's device envelope with its `name` (`driver`, `label`, `poll_s`, `signals`, `inputs`, and the driver's fields flat beside them); 202 `RigEditOut`; 409 name taken; 404 unknown link, or an input address on no device; 422 unknown driver or a config it refuses. A config that validates but will not build is caught at the restart, which goes back to the version before |
| `DELETE` | `/api/devices/{name}` | 202 `RigEditOut`; the controllers on it go with it; 404 no such device; 409 while another device's input is bound to it |
| `POST` | `/api/rig` | body a rig document (`links`, `devices`, `controllers`; other keys ignored), added to the rig; 202 `RigEditOut`; 409 a name the rig already has |
| `GET` | `/api/rig/document` | the running rig as a rig file would build it, defaults left out |
| `GET` | `/api/rig/changes` | what differs from the rig as this run started, as an overlay (a removed key is `null`); `{}` when nothing. An edit restarts the rig, so only a controller attached or removed since the start shows |
| `GET` | `/api/rig/versions` | `[{id, time_ns, reason, files, parent, head}]`, newest first; `?limit=`. `parent`: the version this was made from (the head when it was saved; `null` for a first); `head`: the version the running rig is at, or is restarting to |
| `GET` | `/api/rig/versions/{id}` | the same with `document` |
| `POST` | `/api/rig/versions/{id}/restore` | make the rig that version again, whole: 202 `RigEditOut`, a new version `restored from {id}` on top of the head (its `parent`), built fresh at the restart; 404 no such version; 409 if the rig already is it |
| `GET` | `/api/drivers` | every registered type: `{role: "driver" \| "link", module, description, schema}` (`schema_error` in place of `schema` if pydantic cannot build one) |
| `POST` | `/api/drivers/reload` | re-import the runner's drivers directory (`--drivers`, default `drivers/` beside the first rig file): `{directory, registered: {file: [tags]}, errors: {file: message}}`; a file's earlier tags are dropped first, so an edited driver re-registers; 404 with no directory |
| `POST` | `/api/probe` | `{report}`: the board's buses, GPIO chips and I²C addresses (flyball-linux); `?scan=false` for the list without a bus transaction; 404 where it is not installed. A `POST` because a scan drives every I²C bus |
| `POST` | `/api/links/{name}/query` | body `{text}`; `{reply}` from a text link's `query()`; 409 for a link that is not one |
| `POST` | `/api/rig/save` | body `{path?, overwrite?}`; no path: the overlay `<rig>.d/added.<suffix>` beside the first rig file, holding what that file held when this run started with this run's changes merged on top (an edit already saved itself there; what is left to save is a controller attached or removed since the start), so a later run's save keeps an earlier one's (409 if the runner was not started from a file; unchanged, the file is not rewritten); a path: the whole rig, flattened (409 unless the runner runs with `--allow-save`; 422 a bad suffix; 409 a file the rig was loaded from unless `overwrite`, which then also clears the overlay, kept as `added.<suffix>.prev`; an existing file keeps its own `runner:` section, which is not returned; 409 if that section cannot be read); returns `{path, document, written}`, `written` false when the overlay already said the same |

## Devices

Every device has one name rig-wide, whatever its driver; `/api/devices`
lists them all with their signal trees.

| | | |
| --- | --- | --- |
| `GET` | `/api/devices` | `[DeviceOut]` |
| `GET` | `/api/devices/{name}` | `DeviceOut`; 404 if no device has that name |
| `GET` | `/api/devices/{name}/schema` | the `DeviceSchema` |
| `POST` | `/api/devices/{name}/commands/{command}` | body: the command's arguments; returns `{result, interrupted}`: `result` what the method returned, `interrupted` `[{controller, was}]` for each controller an `interrupts` command put in manual once it had succeeded (empty otherwise); 409 while a controller drives the device and the command has a `mode`, a linked demand or `writes` but does not interrupt (the method is not run); 503 when a linked argument's demand has a limit not known yet (not run); 409 for a command that drives (a `mode`, a linked demand, `writes`, or `long`) while a latch holds the device, unless the rig stop is the only one and the caller is a person -- a setting's command, a simulation's and the device's own stop command are never refused; a command that succeeds on an offline or stopped device restarts its polling (its next read one period later, not at the end of its backoff) |
| `POST` | `/api/devices/{name}/restart` | poll a device again on its period, its next read one period later: one that is offline and backing off, or stopped (given up); clears nothing -- `offline` stays until a read succeeds; `DeviceOut`. 409 while a read of it is in flight (a device hung in its driver is not waited on), or when the old poll loop is still in a read 2 s after being stopped |
| `PUT` | `/api/devices/{name}/write` | body `{name: value, ...}`, names relative to the device (dotted under a namespace: `position.x`), values in each signal's unit; one atomic write, committed at once; returns `{address: WriteOut}` for each signal set; 409 for a signal a controller drives, or a signal that is not writable; 503 `LimitNotKnownError` while a signal's limit follows another signal that has no value yet, or a non-finite one (NaN, inf) -- refused whole, never passed unclamped, the detail naming the bound and its quality (`its limit follows 'dry' (pending)`); 404 for a name not under the device. A write of a [`values`](../2-config/devices/drivers.md#values) device's signal is logged as a `value_written` event under the caller's principal and kept across restarts |
| `PUT` | `/api/signals/{address}` | body a number: the single-signal write; returns `{address: WriteOut}`; 409 if the address is a namespace; 503 while its limit is not known yet, as above |

Both writes, and a device's commands, carry who asked. While the rig is
[latched stopped](../1-running/runner/access.md#the-latch), a person's
write or driving command under `operate` goes through, logged as a
`written_while_stopped` event; an agent's (through MCP) or a service
token's is refused with 409, as is any write to what an `on_fault: stop` or
`stop_device` latch holds. A write that a demand's
[`permissive`](../2-config/devices/index.md#permissive-a-write-only-while-another-signal-allows-it)
does not allow is 409, naming the signal and the band; a write of the
demand's resolved stop value is always allowed.

A `DeviceOut` is `{name, label, kind, driver, class_name, link, poll_s, signals,
commands, inputs, consumers, sources, readable, writable, conditions, run}`: `kind` is
`device`, or `simulation` for an application's own simulation device (see
[Simulation](#simulation)), `driver` the rig file's `driver:` (null for a device
built in code), `class_name` its Python class, `link` the rig file's name for the link
it was built on (or null), `signals` the tree, `commands` `[CommandOut]`,
`inputs` `{name: InputOut}` — each input, declared or given only by the rig
file: `{name, label, quantity, unit, bound, constant?, quality, reason?,
age_s?}`, where `bound` is the address it follows (null for a number, or
once its source was removed), `constant` the number for an input bound to
one, and `quality`/`reason`/`age_s` the source's now (`pending` before its
first reading; a number is `ok`) -- `consumers` `{path: [binding]}`, who
follows each of this device's signals (`{"dry_supply": ["blender.inputs.dry"]}`;
a signal nobody follows is left out) -- `sources` `{path: {origin, initial,
actor, written_ns}}`, for a `values` device where each value in force came
from: `rig_file`, `restored` (kept from an earlier run: "restored, written
by `actor` at `written_ns`", wall time) or `written` (in this run) --
`readable`/`writable` whether it implements `read`/`commit`,
`conditions` what the rig's condition store holds on the device and its
signals (`offline`, `hung`, `slow`, `write_failed`, and the
driver's own, such as the sim's `broken`), and `run` `{period_s, running,
last_read_ns, read_s, missed, reading_since_ns, consecutive_failures,
next_retry_ns}` for a polled device (null otherwise): `read_s` is how long
the last read took (the driver's `read` alone, in seconds of the rig's
time, not the delivery after it), `missed` how many reads have taken
longer than the period since polling began, `reading_since_ns` when the
read in flight began (rig clock; null when none is), `consecutive_failures`
the reads in a row that raised (0 after one that succeeds), and
`next_retry_ns` when an offline device is next read (rig clock; null
unless it is offline and backing off). An offline device keeps `running:
true` while it retries; `running: false` is a device whose polling was
stopped, or that gave up (`reads.give_up_after_s`).

A signal in the tree is `{name, address, access, role, tags, label,
quantity, unit, dimension, dtype, shape, range, precision, warning, alarm,
poll_s, stale_after_s, limits, initial, quality, readback, on_no_value,
latest, last_usable, write}`: `stale_after_s` the liveness threshold the rig
judges it by now (its own, else `max(3·poll_s, 5 s)` while its device is
polled; null when it is not judged --
[Liveness](../2-config/devices/index.md#liveness-a-signal-that-stops-arriving)), `access` is the set in force as letters (`rp`, `w`,
`rw`, `rpw`), `role` one of `demand`, `readout`, `setting`,
`tags` the section as `{axis: name}` (empty without one), `limits` the
numbers in force now, `quality` `pending` before the first reading and
else the newest reading's ([no value](wire.md#a-reading-with-no-value)),
`readback` a demand's `echo` or `sensed` (null otherwise), `on_no_value` a
banded signal's `fire` or `ignore` with the default resolved (null
unbanded), `latest` `{time_ns, value, quality, reason?, caveats?}` once it
has been read (null before; `value` null when the reading has none),
`last_usable` the newest reading that had a value while `latest` has none
(null otherwise), `write` a `WriteOut` for a writable signal once it has
been set. A namespace is `{name, address, atomic, label,
poll_s, signals: [...]}`, nesting the same shapes.

A `WriteOut` is `{value, requested, at_limit, controller}`: what was last
set after limits, what was asked for when the clamp changed it, `low` /
`high` when the value sits on a limit, and the controller driving the
signal (it refuses manual demands; set its setpoint or detach it).

A `CommandOut` is `{name, description, simulation, commit, mode,
interrupts, writes, demand_of, links}`: `commit` whether the rig commits the
device once the method returns, `mode` what the device's `mode` output
becomes when it runs (if it has one), `interrupts` whether it may run while
a controller drives the device (the controller goes to manual once the
method succeeds), `writes` the demand paths (or a private child's name) it
moves that no argument is linked to, `demand_of` the path of the demand
it sets for a synthesised `set_<name>`, and `links` `{argument: demand
path}` for every argument that is a value for a demand.

`GET /api/devices/{name}/schema` returns `{name, label, class_name, driver,
description, readable, writable, config, signals, inputs, commands}`:
`config` a JSON Schema for the driver's config, `signals` `{path: {address,
access, role, tags, label, quantity, unit, dimension, dtype, value, range,
precision, limits}}` by path relative to the device (`value` a JSON Schema
for the signal's own type), `inputs` `{name: {label, quantity, unit,
bound, constant}}`, and `commands` `{command: {description, arguments, simulation,
commit, mode, interrupts, writes, demand_of}}` — `arguments` a JSON Schema whose
properties linked to a demand also carry `x-signal`, `unit` and
`minimum`/`maximum` from that signal's limits now.

## Reading

| | | |
| --- | --- | --- |
| `GET` | `/api/read/{address}?fresh=` | what the address names: a signal → `{reading: {signal, time_ns, value, quality}}`, and with no value `value: null` plus `reason`, `last_usable` and `age_s` (not an error; [no value](wire.md#a-reading-with-no-value)); an atomic namespace → `{sample: {node, time_ns, values}}`, `values` keyed relative to the node, `null` for one with no value beside its `quality`/`reason`; a device or a namespace read over several transactions → `{samples: [...]}`; `fresh=true` reads the hardware first, which is how a setting (`rw`, never published) is read; 409 when another read of the device (a poll, a fresh read) has been in flight for 5 s; 503 until the first read, 404 for an unknown address |
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
| `POST` | `/api/controllers` | `{output, measured, law?, feedforward?, default?, min_period_s?, setpoint_period_s?, on_fault?}` (the rig file's keys); 201 `ControllerOut`; 409 if the output is already driven or the measured signal already regulated, `feedforward: "setpoint"` across units, or `on_fault: stop` on an output whose stop is `keep`; 404 for an unknown address |
| `DELETE` | `/api/controllers/{address}` | 204; put in manual first, so the output holds its last value; manual demands may drive it again |
| `POST` | `/api/controllers/{address}/regulate` | `{at, start?, tuning?, transfer?}`; `at` a value, `measured`/`setpoint`/`output`, or a generator spec (`{type, ...its own arguments}`, e.g. `{type: "linear_ramp_setpoint", pace, end}`, discriminated by `type` against the `generators` union); `start` says where a generator starts from -- a value, `setpoint` or `measured` (the last reading) -- and defaults to the controller's current setpoint, or its last reading if it has none yet; `output` is converted back to the measured unit through the feedforward's inverse, 422 if it has none; the handover's output is committed at once; 503 if a generator is given and there is neither a setpoint nor a reading to start it from; 409 while a latch holds the controller, its output or the rig -- except that a person's `regulate` clears the controller's own `on_fault: manual` latch first, which holds nothing else |
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

The built-in generators, by `type` (a package's own, registered with
`catalog.register_generator`, is offered in `generators` and accepted in `at`
and in a profile's `segments` alike):

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
| `GET` | `/api/history/sessions/{id}/series/{address}` | `Series {signal: SignalRow, points, downsample}`, a point `{offset_ns, value, flag}`: `value` `null` where the reading had none, with `flag` its code (1 `invalid`, 2 `not_applicable`, 3 `stale`, 4 `stale` with the device offline), else `flag` the value's mark (16/17 `at_limit` low/high) or `null` ([no value](wire.md#a-reading-with-no-value)); query `start_ns`, `end_ns`, and one of `every` (every nth, and every reading with no value), `bucket_ns`, `max_points` (averaged: a bucket with any reading with no value is `null`, with the lowest code in it) |
| `GET` | `/api/history/sessions/{id}/writes/{address}` | `[WriteStateRow {offset_ns, value, requested, at_limit, controller}]`; query `start_ns`, `end_ns` |
| `GET` | `/api/history/sessions/{id}/ticks/{controller}` | `[Tick {controller, offset_ns, mode, correction, measured, setpoint, output, expected, delivered_correction, reapplied}]`; query `start_ns`, `end_ns`. A tick's `correction` is `null` when the law's output was not a number (a NaN integral); `reapplied` is true on a re-apply of a moving setpoint's feedforward between readings, which has no reading (`measured` null) and did not step the law |
| `GET` | `/api/history/sessions/{id}/events` | `[{offset_ns, code, subject, details, id, edge}]`, the events as recorded: `subject` what it is about, `details` `{severity, subject_kind, message, details}`; query `start_ns`, `end_ns`, `code` |
| `GET` | `/api/history/sessions/{id}/spans` | `[Span]`, in start order; nest by `parent_id` |
| `GET` | `/api/history/sessions/{id}/export?format=csv\|json\|zip&layout=wide\|long&step_s=` | the session as a file: `wide` one column per signal (each row holds every signal's last value; `step_s` resamples onto a grid), `long` one row per value (`device, signal, unit, value`), `zip` both (`signals-wide.csv`, `signals-long.csv`) plus each controller's ticks (`controller-{name}.csv`), each write's states (`write-{address}.csv`), `events.csv` and `session.json` (`devices`, `signals`, `controllers`) |
| `GET` | `/api/history/sessions/{id}/series/{address}/export?format=` | one signal as csv/json |
| `GET` | `/api/history/sessions/{id}/writes/{address}/export?format=` | one signal's write states as csv/json |
| `GET` | `/api/history/sessions/{id}/ticks/{controller}/export?format=` | one controller's ticks as csv/json |
| `GET` | `/api/history/sessions/{id}/events/export?format=` | the events as csv/json: `time_s`, `time`, `code`, `subject`, `details` |
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
UI's to define, validated only in outline (`{id, type, label?, x, y, w, h,
config}` on a `grid` of `cols` (12 or 24) × `row_height`). `name` is the
key (the route's `{name}`, a preset's file stem); the document's optional
`label` is what a person reads, and renaming a dashboard in the UI saves a
version with a new `label`. A rig can ship
`dashboards/*.toml`, `*.yaml` or `*.json` beside its file (any format a rig
file itself takes); they are imported on start.

| | | |
| --- | --- | --- |
| `GET` | `/api/dashboards?every=` | `[DashboardRow]`, newest version of each name, this rig's unless `every` |
| `GET` | `/api/dashboards/schema` | JSON Schema of the document |
| `GET` | `/api/dashboards/{name}` | `DashboardWithProblems` |
| `GET` | `/api/dashboards/{name}/history` | `[DashboardRow]`, newest first |
| `PUT` | `/api/dashboards/{name}` | body the document; 201 `DashboardWithProblems`; adds a version; `name` and `rig` are set from the key and the rig |
| `POST` | `/api/dashboards/{name}/rename` | `{name}`; every version moves to the new key (its `label` is kept); 409 if taken |
| `DELETE` | `/api/dashboards/{name}` | 204; every version |

A `DashboardRow` is `{id, name, rig, body, created_ns, sha256}`; a
`DashboardWithProblems` is the same plus `problems: [{widget_id, ref,
reason}]` — every widget whose binding (a `readout`/`gauge`'s `address`, a
`chart`'s `addresses`, a `loop`'s `controller`, a `device`'s `device`, all
inside the widget's own `config`) names something this rig does not
currently have; a readout wants a signal that publishes. The document is
saved and returned as given; nothing is refused for this.

Documents carry `schema_version: 6`. Version 6 renamed a widget's `kind` to
`type` and its `title` to `label`, and gave the document a `label` (a string
or `null`, default `null`: show the name). Version 3 added two fields to version 2:
`readonly` (bool, default `false`: the app disables the dashboard's write
controls for everyone; a convenience, not access control) and `order`
(a number or `null`, default `null`: where its tab sits, ascending, with
unordered dashboards after, newest saved first). An older document is
migrated on read, never refused, and what is stored stays as saved. A
version-5 widget's `kind` and `title` read as its `type` and `label`. A
version-4 `events` widget's `level` (`"WARNING"`) reads as its `severity`
(`"warning"`); a version-3 `program` widget's `interrupt` as its `cancel`. A
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
| `POST` | `/api/sim/device/{command}` | one of its commands; returns what the method returned (bare, not `{result, interrupted}`) |

`GET /api/clock` carries `speed` too, so a client can label a time axis.

## Events

| | | |
| --- | --- | --- |
| `GET` | `/api/events?limit=&severity=` | the last few hundred `Event`s, oldest first; `severity` keeps that severity and above |

An `Event` is `{time_ns, severity, subject_kind, subject, code, message, details, edge}`;
`severity` is `debug`, `info`, `warning` or `error` (the same lowercase
string a condition carries). `subject_kind` is `device`, `signal`,
`controller`, `program` or `rig`, and `subject` names which one. `code` is
one of a fixed set. Most are **point events** (`edge: null`): something
happened. The codes marked *condition* below are held in the rig's
condition store while they last, and their events are **edges**: `edge:
"raised"` when the condition begins (at its own severity) and `edge:
"cleared"` when it ends (`info`, with `details.duration_s`, how long it
held). A condition that is set again while it holds only updates its
message: one `raised` per outage, never one per poll or per step.

| subject_kind | conditions (raised / cleared) | point events |
| --- | --- | --- |
| `device` | `latched` (held by an `on_fault: stop_device`, or a `stop` on a device a command stops), `offline` (after `reads.fail_after` failed reads in a row; cleared by the first read that succeeds), `hung` (`error`: a poll's read in flight past `max(3·poll_s, 5 s)`; cleared when it returns; `details: {reading_s, bound_s}`), `slow`, `write_failed`, and a driver's own | `delivery_failed`, `demand_ignored`, `not_revived` (a command succeeded but its hung poll was not restarted), `gave_up` (retries ran past `reads.give_up_after_s`; polling stopped), `resent` (a commit set a value a failed write had kept), `write_dropped` (a kept value waited past `retry_max_age_s`: not sent) |
| `signal` | `band_warning`, `band_alarm`, `band_unknown` ([Bands](../2-config/devices/index.md#bands)), `latched` (held by an `on_fault: stop`), a driver's own (the sim's `broken`) | `written_while_stopped` (a person's write under the rig stop: `{value, actor}`) |
| `controller` | `step_failed` (a law that raised), `stale_input`, `limit_unknown`, `frozen` (its measured signal has no value: `info` for `not_applicable`, `warning` for a fault; cleared after 3 readings with a value), `not_permitted` (its output's permissive does not allow a write: held), `latched` (`error`: held by an `on_fault` action until its Reset) | `interrupted` (put in manual by a stop), `on_fault` (`{action, reason, accrued_s, was, stop}`), `reseeded` (a ramp resumed after a hold: `{end_was_s, end_s}`) |
| `program` | | `started`, `step`, `step_timed_out`, `step_still_running` (a cancel or a stop gave up waiting for the step, which may still act), `step_failed`, `succeeded`, `failed`, `cancelled` (a person), `interrupted` (the engine, with `details.reason`), `run_from_library` |
| `rig` | `stopped` (`warning`: latched by a software stop, `details: {actor, at_ns, reason}`; cleared by its Reset), `recording_failed` (cleared by the next recording), `edit_not_built` (`error`: the start after a rig edit could not build it and went back to the version before; `details: {version, previous, error}`; held until the next restart) | `delivery_failed`, `stop_applied` (what a stop, a shutdown or a restart's re-applied latch did: `{why, devices, kept}`, `kept` every output left energised with its value), `reset` (a latch let go: `{cause, actor, latch}`), `restored` (an in-place restore, before D-051; no longer raised) |

There is no separate "recovered" code: `offline` cleared is what
`restarted` was, and `write_failed`, `step_failed` and
`limit_unknown` cleared are what `write_recovered` (and `commit_recovered`),
`step_recovered` and `limit_known` were. `commit_failed` is `write_failed`
now, in the store too. `restarted` is kept for the
runner's own restart (not raised yet). Removing a device or detaching a
controller clears what it held, one `cleared` edge each (`details.reason`
`removed` or `detached`). A [`Condition`](wire.md#devices) carries the same
code, its `subject_kind` and `subject`, and `since_ns`; a driver's own may use any
string.

`write_failed` (`error`) is a device's `commit` that raised -- on the
delivery path, or on a blocking device's writer thread: raised once per
outage, with the demands it carried in `details.signals` (the delivery
path's), and held on the device until a commit succeeds (cleared, `info`).
The demands it carried are **kept** (A6): they go out with the device's
next commit, a newer demand on a signal replacing its own, and the rig
retries on its clock with no other traffic -- first after `min(poll_s, 5
s)`, then doubling up to 60 s -- until the value is older than the device's
`retry_max_age_s` (60 s), when it is dropped with a `write_dropped` event
(`details: {signal, value, age_s}`). A commit that sets a kept value raises
`resent` (`re-sent <address>=<value>, staged at <t> s`; `details: {signal,
value, staged_ns}`). The controller driving a failed demand hears
`expected: null` for that tick, and the rest of the delivery -- other
devices' commits, the recorder -- goes on. A manual demand or a command
whose commit raises also gets the error back; its value is kept all the
same. A write that reached the device but whose report to the rig raised is
a `write_failed` too, and its values are sent again (logged; the thread
goes on writing).

While either is held, every **echo demand** on the device (`readback:
echo`, the default: its reading is what the rig committed) reads
`stale`, reason `write_failed`: what the device holds is not known, so a
limit or a settle wait that follows it fails closed and a chart breaks,
its last value kept as `last_usable`. A demand never set yet stays
`pending`. The first commit that succeeds gives every one of them its
last value back; the kept values went out in that same commit. A demand
whose kept value was dropped for its age stays stale until a demand of its
own commits. A device whose reads go `offline`, or whose poll hangs,
gives what its polled reads delivered (readouts, `sensed` demands)
`stale`, reason `device_offline` or `device_hung`, at once; each is `ok`
again with its next read. A published measurement that stops arriving goes
`stale` (`silent`, `last_read`, `never_read`) at its threshold, pushed by
the rig ([Liveness](../2-config/devices/index.md#liveness-a-signal-that-stops-arriving)).

`demand_ignored` (`warning`) is a demand the driver's `commit` never read
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
| `/ws/samples` | the newest published sample per node, and every polled device's run | `{samples?: [SampleOut], runs?: [{name, period_s, running, last_read_ns, read_s, missed, reading_since_ns, consecutive_failures, next_retry_ns, conditions}]}`, either key present only when something in it changed; at most one sample per node, and one run per device, per flush. A value with none is `null`, its `quality` and `reason` in the sample's sparse maps ([no value](wire.md#a-reading-with-no-value)); a flush keeps the newest per signal, so a no-value followed within the flush by a value arrives as the value (history keeps both). A run's `conditions` are what the rig holds on the device now; a condition raised or cleared on it sends the run again |
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
