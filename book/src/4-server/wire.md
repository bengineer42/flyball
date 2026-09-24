# Wire format

How flyball's values cross the wire. Everything is JSON; times are integers;
everything else is named by **address** — see
[HTTP and websocket API](api.md).

JSON has no NaN or infinity. A reading never carries one: the rig makes a
NaN or an infinity from a driver a reading with no value, `null` with its
[quality](#a-reading-with-no-value). A law's state gone wrong is `null` on
every websocket frame and in a controller (`ControllerOut`) over HTTP,
never a bare `NaN` that `JSON.parse` refuses.

## Time

| type | JSON | also accepted on input |
| --- | --- | --- |
| `Time` | `{"seconds": int, "nanoseconds": int}` | — |
| `Duration` | `{"seconds": int, "nanoseconds": int}` | unit keys that add (`{"minutes": 1, "seconds": 30}`), or a bare number of seconds |
| `Rate` | `{"value": float, "per": "second"}` | one `per_<unit>` key: `{"per_minute": 2}` |
| a timestamp field (`time_ns`, `start_ns`, …) | integer nanoseconds | — |

Nanoseconds are integers because a difference of two wall-clock floats
carries ~240 ns of error regardless of the interval. Integer subtraction
first; convert the small result second.

## Signals and quantities

| type | JSON |
| --- | --- |
| `Quantity` | not carried on its own; a signal's `unit` and `dimension` fields say what it is |
| a signal in a device's tree | `{name, address, access, role, tags, label, quantity, unit, dimension, dtype, shape, range, precision, warning, alarm, poll_s, stale_after_s, limits, initial, quality, readback, on_no_value, latest, last_usable, write}` — see [Devices](api.md#devices) |
| `access` | the set in force as lowercase letters: `"rp"`, `"w"`, `"rpw"` |
| `role` | `"demand"`, `"readout"` or `"setting"` |
| `Reading` | `{"signal": address, "time_ns": int, "value": float \| null, "quality": Quality, "reason"?: str, "caveats"?: Caveats, "last_usable"?: LatestOut, "age_s"?: float}` — the optional keys only when there is something to say; see [no value](#a-reading-with-no-value) |
| `LatestOut` | `{"time_ns": int, "value": float \| null, "quality": Quality, "reason"?: str, "caveats"?: Caveats}` — a signal's `latest` and `last_usable` |
| `Sample` | `{"node": address, "time_ns": int, "values": {relative-name: float \| null}, "quality"?: {relative-name: Quality}, "reason"?: {relative-name: str}, "caveats"?: {relative-name: Caveats}, "writes": {relative-name: WriteMetaOut}}` — `values` keyed by dotted paths relative to `node`, never nested; `quality`, `reason` and `caveats` likewise, sparse, and absent when nothing in the sample has one; `writes` present only for the demands the sample includes |
| `WriteMetaOut` | `{requested, at_limit: "low" \| "high" \| null, controller}` — a demand's write record, riding with its reading in a `Sample`; no `value`, already in `values` |
| `WriteOut` | `{value, requested, at_limit: "low" \| "high" \| null, controller}` — `WriteMetaOut` plus the committed value; a signal's `write` (`GET /api/devices`) only, now. For a demand the driver never read (`demand_ignored`) `value` is the reading as it was (`null` with none) and `requested` the demand |

## A reading with no value

A reading's value may be absent: `null`, never a number standing in for
one. Its **quality** says why, and every surface carries it:

| `Quality` | meaning | a fault? |
| --- | --- | --- |
| `ok` | a value | — |
| `pending` | nothing read yet (a signal's `quality` only: no reading carries it) | no |
| `not_applicable` | undefined now (a blend's humidity with no flow); shown as "n/a" | no |
| `invalid` | read, but not a valid measurement (a NAMUR fault current, a sensor's "no measurement", a NaN) | yes |
| `stale` | the rig no longer trusts the last value; `reason` says why | yes |

`reason` is what the driver gave (`ne43_low`, `sensor_failed`, `not
finite`) or, on `stale`, the rig's: `device_offline` (the device's reads
fail), `device_hung` (its poll is stuck in a read), `write_failed` (an echo
demand whose device's writes fail), or, when nothing arrives within the
signal's `stale_after_s`, `silent` (nor from its device), `last_read` (its
device delivers other signals) or `never_read` (never read, past its
deadline). The last three are pushed by the rig at the threshold, as a
reading stamped then: the chart breaks at the rig's threshold, not a
client's guess, so a client needs no timing rule of its own.
First match wins when more than one applies: `stale` (`device_offline`) >
`pending` > `stale` (other reasons) > `invalid` / `not_applicable` > `ok`.

**Caveats** annotate a usable value and gate nothing: `{"at_limit": "low"
| "high"}` when the sensor railed (or the rig clamped a demand) at that
end, so the true value may lie beyond it; `{"out_of_range": "low" |
"high"}` when the value lies outside the signal's own `range`.

On `/ws/samples` a sample's `values` carry the `null`, with the sparse
`quality`/`reason` maps beside them; a chart breaks there. `GET
/api/read/{address}` answers a reading with no value with `value: null`,
its `quality` and `reason`, `last_usable` (the newest reading that had a
value) and `age_s` (how long ago the rig received that, on the rig's
clock). A signal in `GET /api/devices` carries its `quality` (`pending`
before its first reading), with no value now its `last_usable`, and the
threshold it is judged by, `stale_after_s` (null: not judged).

In a recorded session a reading with no value is kept, as `null` with a
**flag** ([`Point.flag`](api.md#history)): 1 `invalid`, 2
`not_applicable`, 3 `stale`, 4 `stale` because the device was offline. A
value's flag is its mark: 16 `at_limit: low`, 17 `at_limit: high`; `null`
for a plain value. Which stale reason (other than `device_offline`) was
not kept; a device's `write_failed` edges say when its writes failed.

An address that does not resolve on the running rig is a 404, named in the
error's message; there is no separate decode-by-registry step the way a
`Channel` once needed one.

A demand's value must be finite. JSON has no NaN or infinity, but the
server's parser takes the literals `NaN`, `Infinity` and `-Infinity`, so
a demand carrying one (`PUT /api/signals/{address}`,
`PUT /api/devices/{name}/write`, a synthesised `set_<name>` command) is
refused with a 422 before anything is applied: a NaN would pass every
limit and rate clamp and reach the device.

## Units

A field's unit rides in its JSON Schema:

```json
{"type": "number", "unit": "°C", "dimension": "Temperature", "minimum": 0}
```

put there by `Measured(unit, ...)` on the Python side. Values are bare
floats in that unit; the framework never converts, so a driver that reads
Kelvin against a °C signal converts before it reports.

## Devices

| type | JSON |
| --- | --- |
| `DeviceOut` | `{name, label, kind, driver, class_name, link, poll_s, signals, commands, inputs, consumers, sources, readable, writable, conditions, run}` — see [Devices](api.md#devices) |
| `InputOut` | `{name, label, quantity, unit, bound, constant?, quality, reason?, age_s?}`: what an input follows (an address, or a number) and its quality now |
| `ValueSourceOut` | `{origin: rig_file \| restored \| written, initial, actor, written_ns}`: where a `values` device's value in force came from; `actor` who wrote it (`null`: not known) |
| `Actor` | `{principal, kind, via, sid, message}` -- who acted, on every record of an action (a stop, a latch, a write, an event's `details.actor`): `principal` the caller's id (`local:console`, `token:<name>`) or the rig's own (`program`, a controller's name, `stop`); `kind` `human`, `service`, `agent`, or `program`, `controller`, `rule`; `via` `http`, `mcp`, `signal` (the break-glass, the runner's shutdown) or `rig` (the rig's own); `sid` the login, `""` for none; `message` anything more (`from 127.0.0.1`) |
| `CommandOut` | `{name, description, simulation, commit, mode, interrupts, writes, demand_of, links}` |
| `CommandRunOut` (`POST .../commands/{command}`) | `{"result": any, "interrupted": [{"controller": str, "was": "regulating"}]}` — `result` what the method returned; `interrupted` the controllers an `interrupts` command put in manual once it had succeeded |
| a device's `config` (`GET .../schema`'s `config`) | a JSON Schema; an instance is `{...fields}` |
| `Condition` | `{"code": str, "severity": "debug" \| "info" \| "warning" \| "error", "message": str, "since_ns": int, "subject_kind": "device" \| "signal" \| "controller" \| "rig", "subject": str, "details": any}` — `subject` is the owner's name (a signal's address) |
| `Event` | `{"time_ns": int, "severity": …, "subject_kind": str, "subject": str, "code": str, "message": str, "details": any, "edge": "raised" \| "cleared" \| null}` — see [Events](api.md#events) |
| `DeviceSchema` (`GET .../schema`) | `{name, label, class_name, driver, description, readable, writable, config, signals, inputs, commands: {command: {description, arguments, simulation, commit, mode, interrupts, writes, demand_of}}}` |
| a command request | one property per method parameter after `self`, from the method's signature; an argument linked to a demand also carries `x-signal`, `unit`, `minimum`/`maximum` |

## Controllers and laws

| type | JSON |
| --- | --- |
| a law config | `{"type": "pi", "kp": 0.5, "ki": 0.05, "tt_s": 0.0}`; the union discriminates on `type` |
| a law view | the config plus the law's state fields (`integral`, `last_raw`, …) |
| `Tuning` | `{"name": name, "config": law config}` |
| `ControllerOut` | `{name, label, output_signal, measured_signal, default, mode, law, feedforward, output_unit, reference, setpoint, arrived, correction, output, expected, delivered_correction, measured, on_fault, latched}` — `name` is `output_signal`; `measured` is a `ReadingOut`; `on_fault` the rig file's form (`"freeze"`, `"manual"`, `"stop"`, `"stop_device"` or `{freeze_s, then}`); `latched` the causes of every latch that refuses its `regulate` now (`["stop"]`, `["on_fault:heaters.heater2"]`), `[]` when none; see [Controllers](api.md#controllers) |
| `mode` | `"manual"`, `"regulating"` (open loop is the `open_loop` law under `"regulating"`) |
| `Transfer` | `"none"`, `"carry"`, `"track"`, `"cold"` |
| `ValueSource` | `"measured"`, `"setpoint"`, `"output"` |

A running law cannot cross the wire; a request that takes one takes a
config or the name of a stored tuning instead.

## Rig edits

| type | JSON |
| --- | --- |
| `RigEditOut` (202 from every [rig edit](api.md#composition)) | `{version, previous, reason, saved, restarting, stop, message}` -- `version` the edit's rig version, now the head; `previous` the head before it (what a start that cannot build it goes back to); `reason` `edited: added device probe` or `restored from 3`; `saved` the overlay it was written to, or `null` for a bare or resumed rig (the store alone); `stop` the stop's report (`POST /api/rig/stop`'s), or `null` if the stop failed; `message` what the edit did, as a sentence. The runner is restarting: the next request may find it down for a moment |

## Stopping and latches

| type | JSON |
| --- | --- |
| `StopReport` (`POST /api/rig/stop`) | `{at_ns, actor, reason, devices, program_interrupted, controllers_manual, interim, latched}` -- `at_ns` wall-clock ns; `actor` an `Actor` (`via` `http`, `mcp` or `signal`); `devices` `{name: DeviceStop}`; `interim` `false` (`true` only from the earlier stop that wrote nothing); `latched` whether the rig is latched stopped now |
| `DeviceStop` | `{state, message, written, kept}` -- `state` `stopped`, `unchanged` or `failed`; `message` what it did, or why it failed; `written` `{address: value}` what it wrote (a driver's readback where it gave one); `kept` `{address: value \| null}` what it left as it was, with the value it holds (`null`: not known) |
| `LatchOut` (`GET /api/rig/latches`, `POST /api/rig/reset`) | `{cause, subjects, actor, at_ns, reason, action}` -- `cause` `stop` or `on_fault:<controller>`; `subjects` `[{subject_kind, subject}]` (`subject_kind` `rig`, `device`, `signal` or `controller`); `actor` the `Actor` who set it: the person or agent for a stop, the controller (`kind: controller`, `via: rig`) for a fault; `action` the fault's `manual`, `stop` or `stop_device`, `""` for a stop |
| `StopPlan` (`GET /api/rig/stop`) | `{stopped, outputs}` -- `stopped` the rig stop's `LatchOut` or `null`; `outputs` `[{address, device, stop, origin, command, controller, covered_if_flyball_dies, warnings}]`: `stop` a number, `"keep"`, or `null` when `command` runs; `origin` `off`, `you_said`, `nobody_said` or `command`; `covered_if_flyball_dies` always `false`; `warnings` `[str]` |
| `Health.stopped`, `Health.latches` (`GET /api/health`) | `stopped` `{actor, at_ns, reason}` or `null`; `latches` `[{subject_kind, subject, cause}]`, one row per subject a latch holds |

## Sessions

A `SessionRow` is `{id, start_ns, end_ns, version, config, hardware,
details, rig_version_id, kind, pinned, continues, bytes}`. `kind` is
`"session"` (a recording) or `"scratch"` (the runner's rolling record);
`pinned` exempts it from ageing out; `continues` is the id of the session
this one carried on from at a rotation boundary, else `null`; `bytes` is an
estimate of what a scratch session holds, refreshed each sweep, `null` for
a recording. Every offset in a session (`offset_ns` on a reading, a write
state, a tick, an event) counts from `start_ns`; for a scratch session
`start_ns` is the oldest reading it still holds and moves forward as it
trims. `end_ns` is `null` while the session is open.

## Labelled enums

An enum whose members carry a display label (`Transfer`, `ValueSource`,
`TimeUnit`) serialises as the value; the label appears in the JSON Schema as
the option's title, so a form can show it without a second copy of the
options.

## Errors

`{"detail": "<message>"}`, with the status code from the error's base. See
[HTTP and websocket API](api.md#errors). A refused websocket is accepted
and then closed, so the client sees why: 4401 without a credential, or with
one that is wrong, revoked or expired, or an anonymous caller lacking the
verb (the UI stops retrying, and offers sign-in); 4403 for a signed-in
caller lacking the verb; 1013 when the front already holds as many
sockets to the rig as it allows (try again later: the UI retries); 1014
when the front and the runner are out of step; 4404 for a path outside the
runner's `--root-path`. An open socket whose runner ends without closing it
(killed, or crashed) is closed by the front with 1011, not dropped, so a
client can tell that from a lost network (1006); the UI reconnects after
either. A `403` for a
missing verb says which: `{"detail", "needed": "operate"}`. The session
cookie (`flyball-<port>` or `__Host-flyball` at a front,
`flyball-bare-<port>` at a bare runner; [authentication](api.md#authentication))
is opaque to a client.
