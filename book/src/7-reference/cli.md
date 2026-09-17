# CLI reference

```
flyball [--url URL] [--timeout S] [--json] [--refresh] [--offline SCHEMA.json] <command> ...
```

| option | |
| --- | --- |
| `--url URL` | runner base URL; default `$FLYBALL_URL` or `http://127.0.0.1:8000`; with the prefix for a runner started with `--root-path` (`http://host/furnace`) |
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
| `status` | `GET /api/health` | devices, controllers, waits, recording, in one screen |
| `schema` | `GET /api/schema` | the whole document |
| `devices` | `GET /api/devices` | name, label, driver, signal tree per device |
| `controllers` | `GET /api/controllers` | every controller's view |
| `demand ADDRESS VALUE` | `PUT /api/signals/ADDRESS` | put a value on a writable signal |
| `read ADDRESS [--fresh]` | `GET /api/read/ADDRESS` | a signal's reading, a namespace's sample, or a device's samples |
| `clock` | `GET /api/clock` | `start_time_ns`, `now_ns`, `elapsed_ns`, `tags`, `speed` |
| `waits` | `GET /api/waits` | what the rig is waiting on |
| `wait fire NAME` | `POST /api/waits/NAME/fire` | settle the wait as met |
| `wait interrupt NAME` | `POST /api/waits/NAME/interrupt` | cancel it |
| `watch STREAM` | `/ws/STREAM` | one JSON line per frame; `samples`, `controllers`, `writes`, `devices`, `waits`, `events` |
| `sessions` / `export ID` | `/api/history/*` | recorded sessions; export as Bluesky event-model documents |
| `program check FILE` / `program run FILE` / `program status` / `program stop` | `/api/programs/*` | validate, start, watch, stop a program |
| `sim` / `sim clock N` / `sim set PLANT k=v` / `sim reset` / `sim config` / `sim save` | `/api/sim/*` | a simulated rig's knobs |

## Without a rig

| command | | |
| --- | --- | --- |
| `rig check FILE...` | validate one or more rig files (later overlays earlier); prints the merge |
| `rig schema` | the rig file's JSON Schema, for an editor (`# yaml-language-server: $schema=`) |
| `program schema` | the program file's JSON Schema |
| `program check --local FILE` | validate a program file against the commands installed here |
| `new NAME` | write `NAME.py`: a complete device driver with a tag, ready to edit |
| `password [PASSWORD]` | print the hashed line for `daemon.auth.password`; asks without echo when none is given |

## Device subcommands

One per device, named after it, built from the schema.

| command | reads | |
| --- | --- | --- |
| `NAME` | `GET /api/devices/NAME` | the signal tree, conditions, readable/writable |
| `NAME schema` | the cached schema | the config schema, every signal's and command's |
| `NAME COMMAND [flags]` | `POST /api/devices/NAME/commands/COMMAND` | run a command |

Flags per argument:

| schema | flag |
| --- | --- |
| a property `x` | `--x VALUE` |
| a nested object `a` with property `b` | `--a.b VALUE` |
| a boolean | `--x` / `--no-x` |
| an enum, or a `oneOf` of constants | `--x` with choices; per-option titles in `--help` |
| a discriminated `oneOf` | `--a.tag KIND` selects the branch, then that branch's flags |
| a single argument | may also be given positionally: `set_limit 0.5` |

Any flag also takes a JSON literal: `--x '[1, 2]'`, `--x '{"k": 1}'`.
`"4"` parses as the number 4; `"on"` stays a string.

## Exit codes

| | |
| --- | --- |
| 0 | done |
| 1 | the rig refused, or the arguments failed the schema; the message is the server's or the schema's own |
| 3 | the rig was unreachable |
| 130 | interrupted |
