# Wire format

How flyball's values cross the wire. Everything is JSON; times are integers;
everything else is named by **address** — see
[HTTP and websocket API](api.md).

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
| a signal in a device's tree | `{name, address, access, role, tags, label, quantity, unit, dimension, dtype, shape, range, precision, warn, alarm, poll_s, limits, initial, latest, write}` — see [Devices](api.md#devices) |
| `access` | the set in force as lowercase letters: `"rp"`, `"w"`, `"rpw"` |
| `role` | `"demand"`, `"output"`, `"setting"` or `"config"` |
| `Reading` | `{"signal": address, "time_ns": int, "value": float}` |
| `Sample` | `{"node": address, "time_ns": int, "values": {relative-name: float}, "writes": {relative-name: WriteMetaOut}}` — `values` keyed by dotted paths relative to `node`, never nested; `writes` likewise, present only for the demands the sample includes |
| `WriteMetaOut` | `{requested, at_limit: "low" \| "high" \| null, controller}` — a demand's write record, riding with its reading in a `Sample`; no `value`, already in `values` |
| `WriteOut` | `{value, requested, at_limit: "low" \| "high" \| null, controller}` — `WriteMetaOut` plus the committed value; a signal's `write` (`GET /api/devices`) only, now |

An address that does not resolve on the running rig is a 404, named in the
error's message; there is no separate decode-by-registry step the way a
`Channel` once needed one.

A demand's value must be finite. JSON has no NaN or infinity, but the
server's parser takes the literals `NaN`, `Infinity` and `-Infinity`, so
a demand carrying one (`PUT /api/signals/{address}`,
`PUT /api/devices/{name}/demand`, a synthesised `set_<name>` command) is
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
| `DeviceOut` | `{name, label, kind, driver, type, link, poll_s, signals, commands, inputs, readable, writable, conditions, run}` — see [Devices](api.md#devices) |
| `CommandOut` | `{name, description, simulation, commit, mode, interrupts, demand_of, links}` |
| a device's `config` (`GET .../schema`'s `config`) | a JSON Schema; an instance is `{...fields}` |
| `Condition` | `{"kind": str, "level": 10 \| 20 \| 30 \| 40, "message": str, "since_ns": int}` |
| `DeviceSchema` (`GET .../schema`) | `{name, label, type, driver, description, readable, writable, config, signals, inputs, commands: {tag: {description, arguments, simulation, commit, mode, interrupts, demand_of}}}` |
| a command request | one property per method parameter after `self`, from the method's signature; an argument linked to a demand also carries `x-signal`, `unit`, `minimum`/`maximum` |

## Controllers and laws

| type | JSON |
| --- | --- |
| a law config | `{"tag": "PI", "kp": 0.5, "ki": 0.05, "tt": 0.0}`; the union discriminates on `tag` |
| a law view | the config plus the law's state fields (`integral`, `last_raw`, …) |
| `Tuning` | `{"tag": name, "config": law config}` |
| `ControllerOut` | `{name, label, target, source, default, mode, law, feedforward, demand_unit, reference, setpoint, correction, demand, expected, delivered_correction, reading}` — `name` is `target`; see [Controllers](api.md#controllers) |
| `mode` | `"manual"`, `"open"`, `"regulating"` |
| `Transfer` | `"none"`, `"carry"`, `"track"`, `"reset"` |
| `ValueSource` | `"process"`, `"setpoint"`, `"demand"` |

A running law cannot cross the wire; a request that takes one takes a
config or the name of a stored tuning instead.

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
one that is wrong, revoked or expired (the UI stops retrying); 4403 for a
caller lacking the verb; 1014 when the front and the runner are out of
step; 4404 for a path outside the runner's `--root-path`. A `403` for a
missing verb says which: `{"detail", "needed": "operate"}`. The session
cookie (`flyball-<port>` or `__Host-flyball` at a front,
`flyball-bare-<port>` at a bare runner; [authentication](api.md#authentication))
is opaque to a client.
