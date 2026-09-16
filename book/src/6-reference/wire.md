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
| a signal in a device's tree | `{name, address, access, label, quantity, unit, dimension, dtype, shape, range, precision, warn, alarm, poll_s, limits, together, latest, write}` — see [Devices](api.md#devices) |
| `access` | the set in force as lowercase letters: `"rp"`, `"w"`, `"rpw"` |
| `Reading` | `{"signal": address, "time_ns": int, "value": float}` |
| `Sample` | `{"node": address, "time_ns": int, "values": {relative-name: float}}` — keys are dotted paths relative to `node`, never nested |
| `WriteOut` | `{value, requested, at_limit: "low" \| "high" \| null, controller}` |

An address that does not resolve on the running rig is a 404, named in the
error's message; there is no separate decode-by-registry step the way a
`Channel` once needed one.

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
| `DeviceOut` | `{name, label, kind, driver, type, link, poll_s, signals, commands, state, conditions, run}` — see [Devices](api.md#devices) |
| a device view (`GET .../schema`'s `config`/`settings`/`state`) | each a JSON Schema; an instance is `{...fields}` |
| `Condition` | `{"kind": str, "level": 10 \| 20 \| 30 \| 40, "message": str, "since_ns": int}` |
| `DeviceSchema` | `{name, label, type, driver, description, config, settings, state, signals, commands: {tag: {description, arguments, simulation}}}` |
| a command request | one property per method parameter after `self`, from the method's signature |

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

## Labelled enums

An enum whose members carry a display label (`Transfer`, `ValueSource`,
`TimeUnit`) serialises as the value; the label appears in the JSON Schema as
the option's title, so a form can show it without a second copy of the
options.

## Errors

`{"detail": "<message>"}`, with the status code from the error's base. See
[HTTP and websocket API](api.md#errors).
