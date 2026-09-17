# Devices and signals

!!! tip "In the browser"
    [Devices](../ui/devices.md) is the same view: a card per device, its signals and commands; [Charts](../ui/charts.md) is `watch` with a picture.

| command | |
| --- | --- |
| `flyball status` | one screen: devices (signals with their latest values and write states), controllers, waits, recording (`GET /api/health`) |
| `flyball devices` | every device: name, label, driver, signal tree (`GET /api/devices`) |
| `flyball read ADDRESS [--fresh]` | a signal's reading, a namespace's sample, or a device's samples; `--fresh` reads the hardware now (`GET /api/read/{address}`) |
| `flyball demand ADDRESS VALUE` | put a value on a writable signal -- what typing into a target box does (`PUT /api/signals/{address}`) |
| `flyball watch STREAM` | follow `samples`, `controllers`, `waits` or `events` as one JSON line per frame (`/ws/{stream}`) |
| `flyball clock` | the rig's timebase (`GET /api/clock`) |
| `flyball schema` | the schema document, for saving or `jq` (`GET /api/schema`) |

## Device commands

Three fixed subcommands take the device's name as an argument, rather than
each device growing its own subcommand tree:

```
flyball view heater                              the signal tree, conditions, readable/writable
flyball device-schema heater                      the config schema, every signal's and command's
flyball invoke heater set_limit limit=0.5         run a command, KEY=VALUE
flyball invoke heater set_limit '{"limit": 0.5}'  or a single raw JSON object
flyball invoke heater off
```

`invoke`'s trailing arguments are either `KEY=VALUE` pairs or one JSON
object -- there's no dotted-flag nesting or per-argument `--flag`/units in
`--help` the way a schema-driven argparse tree would have; `"4"` parses as
the number 4, `"on"` stays a string.

A device's commands, their arguments and hints are the same the UI draws as
command cards; both come from the device's schema (`device-schema`).
