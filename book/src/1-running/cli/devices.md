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

## Device subcommands

Every device is a subcommand named after it:

```
flyball heater                 the signal tree, conditions, readable/writable
flyball heater schema          the config schema, every signal's and command's
flyball heater --help          the device's docstring and its commands
```

and every `@command` is a subcommand under it, with a flag per argument:

```
flyball heater set_limit --limit 0.5
flyball heater set_limit 0.5           # one argument may be given positionally
flyball heater off
```

Flags come from the argument schema: `--name` per property, dotted for
nested objects (`--flow.tag absolute --flow.flow 8`), `--flag/--no-flag` for
booleans, choices for enums. Help text is the docstrings, units and bounds
the schema carries. Any flag also takes a JSON literal, for shapes the flags
cannot spell.

A device's commands, their arguments and hints are the same the UI draws as
command cards; both come from the device's schema.
