# CLI reference

```
flyball [--url URL] [--timeout S] [--json] [--refresh] [--offline SCHEMA.json] <command> ...
```

| option | |
| --- | --- |
| `--url URL` | daemon base URL; default `$FLYBALL_URL` or `http://127.0.0.1:8000` |
| `--timeout S` | seconds per request; default 5 |
| `--json` | print raw JSON, one document per line |
| `--refresh` | fetch the schema again rather than use the cache |
| `--offline SCHEMA.json` | build the command tree from a saved schema; no rig needed for `--help` |

The schema is cached per URL under `$XDG_CACHE_HOME/flyball` (default
`~/.cache/flyball`). If the rig is unreachable and a cache exists, the cache
is used.

## Fixed subcommands

| command | reads | |
| --- | --- | --- |
| `schema` | `GET /api/schema` | the whole document |
| `actuators` | `GET /api/actuators` | name, type, summary per actuator |
| `readers` | `GET /api/readers` | the same for readers |
| `sources` | `GET /api/sources` | every source with its channels and latest sample |
| `loops` | `GET /api/loops` | every loop's view |
| `clock` | `GET /api/clock` | `start_time_ns`, `now_ns`, `elapsed_ns`, `tags` |
| `signals` | `GET /api/signals` | what the rig is waiting on |
| `signal fire NAME` | `POST /api/signals/NAME/fire` | settle the wait as met |
| `signal interrupt NAME` | `POST /api/signals/NAME/interrupt` | cancel it |
| `watch STREAM` | `/ws/STREAM` | one JSON line per frame; `samples`, `loops`, `actuators`, `readers`, `signals` |

## Device subcommands

One per actuator and reader, named after the device, built from the schema.

| command | reads | |
| --- | --- | --- |
| `NAME` | `GET /api/{actuators,readers}/NAME` | config, settings, state |
| `NAME schema` | the cached schema | config, settings, state and command schemas |
| `NAME COMMAND [flags]` | `POST /api/…/NAME/COMMAND` | run a command |

Flags per argument:

| schema | flag |
| --- | --- |
| a property `x` | `--x VALUE` |
| a nested object `a` with property `b` | `--a.b VALUE` |
| a boolean | `--x` / `--no-x` |
| an enum, or a `oneOf` of constants | `--x` with choices; per-option titles in `--help` |
| a discriminated `oneOf` | `--a.tag KIND` selects the branch, then that branch's flags |
| a single argument | may also be given positionally: `set_flows 2 6` |

Any flag also takes a JSON literal: `--x '[1, 2]'`, `--x '{"k": 1}'`.
`"4"` parses as the number 4; `"on"` stays a string.

## Exit codes

| | |
| --- | --- |
| 0 | done |
| 1 | the rig refused, or the arguments failed the schema; the message is the server's or the schema's own |
| 3 | the rig was unreachable |
| 130 | interrupted |
