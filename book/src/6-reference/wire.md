# Wire format

How flyball's values cross the wire. Everything is JSON; times are integers.

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

## Readings

| type | JSON |
| --- | --- |
| `Measurand` | its name; decoded by registry lookup |
| `Channel` | `{"source": name, "measurand": name}`; decoded by lookup on the source. A trace naming a source this process lacks fails validation |
| `ChannelOut` | `{source, measurand, unit, label, range, precision}` — the channel plus what a gauge needs |
| `SampleOut` | `{"seq": int, "time_ns": int, "values": {measurand: float}}` |
| `ReadingOut` | `{"time_ns": int, "value": float}` |
| `SourceOut` | `{"name", "channels": [ChannelOut], "latest": SampleOut \| null}` |

## Units

A field's unit rides in its JSON Schema:

```json
{"type": "number", "unit": "°C", "dimension": "Temperature", "minimum": 0}
```

put there by `Quantity(unit, ...)` on the Python side. Values are bare
floats in that unit.

## Devices

| type | JSON |
| --- | --- |
| a device view | `{"config": {...}, "settings": {...}, "state": {...}}` |
| `Condition` | `{"kind": str, "level": 10 \| 20 \| 30 \| 40, "message": str, "since_ns": int}` |
| `DeviceSchema` | `{name, type, description, config, settings, state, commands: {tag: {description, arguments}}}` |
| a command request | one property per method parameter after `self`, from the method's signature |

## Loops and laws

| type | JSON |
| --- | --- |
| a law config | `{"tag": "PI", "kp": 0.5, "ki": 0.05, "tt": 0.0}`; the union discriminates on `tag` |
| a law view | the config plus the law's state fields (`integral`, `last_raw`, …) |
| `Tuning` | `{"tag": name, "config": law config}` |
| `LoopOut` | `{name, channel: ChannelOut, default, mode, law: view, reference, correction, demand, expected, delivered_correction, reading: ReadingOut}` |
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
