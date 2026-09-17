# CLI reference

`flyball` is a standalone Go binary (`daemon/cmd/flyball`) -- build it with
`cd daemon && go build ./cmd/flyball` (no install script or release binary
yet; see [Installing](../1-running/runner/index.md#installing)). It talks to
a runner over the same HTTP/websocket API any other client uses, or to a
`flyballd` daemon in front of several runners.

```
flyball [-s NAME] <command> ...
```

| addressing | | |
| --- | --- | --- |
| `-s`/`--server NAME` | talk to the runner named `NAME`, through a daemon | uses `$FLYBALLD_URL` (default `http://127.0.0.1:9000`) |
| neither given | talk to one runner directly | uses `$FLYBALL_URL` (default `http://127.0.0.1:8000`), with the prefix for a runner started with `--root-path` (`http://host/furnace`) |

There is no `--url`, `--token`, `--offline` or schema-caching flag, and no
per-device subcommand tree built from the schema -- those were `cli.py`'s
(the old Python CLI, removed); the Go CLI's device commands are the fixed
`view`/`device-schema`/`invoke` below instead. A runner started with a
token has no CLI-side support yet (only the UI sends one).

## Runner-addressed subcommands

| command | reads/writes | |
| --- | --- | --- |
| `status [--json]` | `GET /api/health` + devices/controllers/waits | devices, controllers, waits, recording, in one screen |
| `schema` | `GET /api/schema` | the whole document |
| `devices` | `GET /api/devices` | name, label, driver, signal tree per device |
| `controllers` | `GET /api/controllers` | every controller's view |
| `demand ADDRESS VALUE` | `PUT /api/signals/ADDRESS` | put a value on a writable signal |
| `read ADDRESS [--fresh]` | `GET /api/read/ADDRESS` | a signal's reading, a namespace's sample, or a device's samples |
| `clock` | `GET /api/clock` | `start_time_ns`, `now_ns`, `elapsed_ns`, `tags`, `speed` |
| `waits` | `GET /api/waits` | what the rig is waiting on |
| `wait fire NAME` | `POST /api/waits/NAME/fire` | settle the wait as met |
| `wait interrupt NAME` | `POST /api/waits/NAME/interrupt` | cancel it |
| `watch STREAM` | `/ws/STREAM` | one JSON line per frame; `samples`, `controllers`, `writes`, `signals` |
| `view DEVICE` | `GET /api/devices/DEVICE` | one device's signal tree |
| `device-schema DEVICE` | `GET /api/schema` (the `devices.DEVICE` branch) | one device's config/signal/command schemas |
| `invoke DEVICE COMMAND [KEY=VALUE ...\|JSON]` | `POST /api/devices/DEVICE/commands/COMMAND` | run a device command |
| `sessions` | `GET /api/history/sessions` | recorded sessions, newest first |
| `export SESSION [--format csv\|json\|zip] [--out PATH]` | `GET /api/history/sessions/SESSION/export` | a session as a table; written to `PATH` or stdout |
| `program check\|run\|status\|stop PATH` | `/api/programs/*` | validate, start, watch, stop a program (`run` takes `[--interrupt]`) |
| `sim show\|clock\|step\|set\|reset\|config\|save` | `/api/sim/*` | a simulated rig's knobs |

`invoke`'s trailing arguments are either `KEY=VALUE` pairs or a single raw
JSON object -- there is no dotted-flag nesting or per-argument `--flag`
(that was schema-driven argparse, `cli.py`-only); `"4"` parses as the
number 4, `"on"` stays a string, matching the old CLI's literal parsing.

## Local (no runner or daemon involved)

| command | | |
| --- | --- | --- |
| `rig check FILE... [--set KEY=VALUE] [--print]` | validate one or more rig files (later overlays earlier) against the embedded rig schema and the same hand-written cross-field rules `RigConfig` enforces; prints a one-line summary, and the merged document with `--print` |
| `rig schema` | the rig file's JSON Schema, for an editor (`# yaml-language-server: $schema=`) |
| `program schema` | the program file's JSON Schema |
| `program check --local FILE` | validate a program file against the commands installed here |
| `run RIG-FILE [flyball-runner flags...]` | start a runner directly in the foreground, no daemon involved -- the escape hatch for "just run one rig" |
| `password [PASSWORD]` | hash a password for `runner.auth.password` (prompts if omitted) |
| `new NAME [--dir PATH]` | write `NAME.py`: a complete device driver with a tag, ready to edit |

!!! note "`rig check --print`'s formatting"
    The Go CLI's `--print` prints the same merged document as the old
    Python tool, but reordered (keys alphabetical, not the model's
    declared order) and reformatted (a whole-number float prints without
    `.0`; a schema-defaulted field like `recording: false` that isn't in
    the input files doesn't appear). Same data, not byte-identical output.

There is no `program check --local FILE` -- `program check` always
validates against a running rig (`POST /api/programs/check`); the old
Python CLI's offline-against-installed-commands mode isn't ported.

## Daemon-managed (via `$FLYBALLD_URL`, never routed through a runner)

| command | | |
| --- | --- | --- |
| `daemon runners` | `GET /api/runners` | list registered runners |
| `daemon start MANIFEST.json` | `POST /api/runners` | register and start one |
| `daemon stop NAME` | `DELETE /api/runners/NAME` | stop and deregister it |
| `daemon restart NAME` | `POST /api/runners/NAME/restart` | restart it |
| `logs NAME` | `GET /api/runners/NAME/logs` | its captured stdout/stderr |

## Exit codes

| | |
| --- | --- |
| 0 | done |
| 1 | the rig, the daemon, or a local check refused; the message is theirs |
| 2 | no command given |
