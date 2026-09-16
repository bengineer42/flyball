# The CLI

`flyball` is a command line built from the rig's schema. It fetches
`GET /api/schema` once, caches it under `~/.cache/flyball`, and builds a
subcommand per device and per device command. Nothing about any particular
device is written into it.

```
flyball --url http://pi:8000 devices        # or export FLYBALL_URL
```

## Fixed subcommands

| | |
| --- | --- |
| `flyball status` | one screen: devices (signals with their latest values and write states), controllers, waits, recording |
| `flyball devices` / `controllers` | list what the rig has |
| `flyball demand ADDRESS VALUE` | put a value on a writable signal (`PUT /api/signals/{address}`) |
| `flyball read ADDRESS [--fresh]` | a signal's reading, a namespace's sample, or a device's samples (`GET /api/read/{address}`) |
| `flyball clock` | the rig's timebase |
| `flyball waits` | what the rig is waiting on |
| `flyball wait fire NAME` / `interrupt NAME` | answer or cancel a wait |
| `flyball watch STREAM` | follow `samples`, `controllers`, `writes` or `signals` as JSON lines |
| `flyball schema` | the schema document, for saving or `jq` |
| `flyball sessions` / `export ID` | recorded sessions; one as Bluesky event-model documents |
| `flyball program check FILE` | the rig normalises and validates a program file; nothing runs |
| `flyball program run FILE [--interrupt]` / `program status` / `program stop` | start a program file on the rig, see where it is, stop it |
| `flyball sim` / `sim clock N` / `sim set PLANT k=v` / `sim reset` / `sim config` / `sim save` | a simulated rig's knobs ([simulation](../4-control/simulation.md)) |

## Without a rig

These work with no daemon reachable; they use the configs installed here:

| | |
| --- | --- |
| `flyball rig check FILE` | validate a rig file: drivers, links, names, controllers |
| `flyball rig schema` | the rig file's JSON Schema, for an editor (`#:schema` in TOML) |
| `flyball program schema` | the program file's JSON Schema |
| `flyball program check --local FILE` | validate a program file against the commands installed here |
| `flyball new NAME` | write `NAME.py`: a complete device driver with a tag, ready to edit |

## Device subcommands

Every device is a subcommand named after it:

```
flyball heater                 config, settings and state
flyball heater schema          the three schemas and every command's
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

## Output

Human-readable by default; `--json` prints one JSON document per line, for
piping. Exit codes: 0; 1 for an error the rig reported; 3 if the rig was
unreachable.

## Offline

`flyball --offline schema.json --help` builds the whole tree from a saved
schema, so `--help` works with no rig. If the rig is unreachable and a cached
schema exists, the cache is used: stale is better than no `--help` at all.

## The client underneath

The CLI is `flyball.client.Rig` with argparse in front. The client is usable
on its own and imports nothing from the rig:

```python
from flyball.client import Rig

rig = Rig("http://pi:8000")
rig.devices.heater.set_limit(limit=0.5)
rig.devices.probe.view()["state"]
rig.demand("heaters.heater1", 1200.0)
for frame in rig.watch("controllers"):
    ...
```

Arguments are validated against the command's schema before anything is
sent, so a wrong call fails locally with the schema's own words.
