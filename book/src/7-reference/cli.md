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

There is no `--url`, `--offline` or schema-caching flag, and no per-device
subcommand tree built from the schema -- those were `cli.py`'s (the old
Python CLI, removed); the Go CLI's device commands are the fixed
`view`/`device-schema`/`invoke` below instead. `--token TOKEN`/`$FLYBALL_TOKEN`
sends a bearer token on every request; `login`/`logout` (below) trade a
password or token for a session cookie instead, so it doesn't need repeating.

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
| `login [SECRET]` | `POST /api/auth/login` | trade a password or token for a session cookie, persisted for later invocations; prompts if `SECRET` omitted |
| `logout` | | drop the saved session cookie |

`invoke`'s trailing arguments are either `KEY=VALUE` pairs or a single raw
JSON object -- there is no dotted-flag nesting or per-argument `--flag`
(that was schema-driven argparse, `cli.py`-only); `"4"` parses as the
number 4, `"on"` stays a string, matching the old CLI's literal parsing.

## Local (no runner or daemon involved)

| command | | |
| --- | --- | --- |
| `rig check FILE... [--set KEY=VALUE] [--print]` | validate one or more rig files (later overlays earlier) against the embedded rig schema and the same hand-written cross-field rules `RigConfig` enforces; prints a one-line summary, and the merged document with `--print`. The schema holds the drivers and links of every first-party package (the engine, `flyball-sim`, `-modbus`, `-visa`, `-chips`, `-linux`, `-qcodes`, `-pymeasure`, and `examples/furnace`), not those of a package of your own; and a `board:` profile is not applied, so a device that names a `pin:` is refused here though the runner accepts it |
| `rig schema` | the rig file's JSON Schema, for an editor (`# yaml-language-server: $schema=`) |
| `program schema` | the program file's JSON Schema |
| `run RIG-FILE [--serve-ui ADDR] [--port PORT] [--uv] [flyball-runner flags...]` | start a runner directly in the foreground, no daemon involved -- the escape hatch for "just run one rig". `--uv` runs it via `uv run --project <rig file's directory> flyball-runner` instead of a bare exec, so it works outside an app's own uv-managed venv (e.g. `examples/humidity`, `examples/furnace`) without first `cd`-ing there. `--serve-ui ADDR` additionally serves the embedded dashboard UI on `ADDR`, reverse-proxying `/api`, `/ws` and `/mcp` to the runner -- no separate reverse proxy needed. `--port PORT` is where the UI proxy expects the runner to be listening (default `8000`; set the runner's own `runner.port` in the rig file to match if it differs). All three have a rig-file equivalent -- `runner.run.serve_ui`, `runner.run.uv`, `runner.run.port` -- read from the rig file (`extends` resolved) as the default when the matching flag isn't given; a flag on the command line always wins. `runner.run` is Go-CLI-only: `flyball-runner`'s own config (`RunnerConfig`) accepts the key but never reads or validates its contents |
| `password [PASSWORD]` | hash a password for `runner.auth.password` (prompts if omitted) |
| `new NAME [--dir PATH]` | write `NAME.py`: a complete device driver with a tag, ready to edit |

!!! note "`rig check --print`'s formatting"
    The Go CLI's `--print` prints the same merged document as the old
    Python tool, but reordered (keys alphabetical, not the model's
    declared order) and reformatted (a whole-number float prints without
    `.0`; a schema-defaulted field like `recording: false` that isn't in
    the input files doesn't appear). Same data, not byte-identical output.

`program check` always validates against a running rig
(`POST /api/programs/check`, the runner-addressed table above); there is
no local, offline-against-installed-commands mode as `cli.py` had.

## The daemon

`flyballd` starts one `flyball-runner` per manifest and proxies each under
its `root_path`; `/` lists them. `flyballd --config flyballd.yaml`; every key has a default:

| key | default | |
| --- | --- | --- |
| `listen` | `127.0.0.1:9000` | the address it serves on. A client has 10 s to send its request headers, in at most 64 KiB, and an idle keep-alive connection is closed after 120 s; a response has no time limit, so log streaming and websockets proxied to a runner stay open |
| `manifests_dir` | `manifests` | one `NAME.yaml` per runner: `name`, `server_config` (the runner's rig file), `port`, and optionally `host` (default `127.0.0.1`; a loopback address only -- the runner is reached through `flyballd`'s proxy, and anything else, `0.0.0.0` included, is refused), `root_path` (default `/NAME`), `restart` (below), `enabled`, `uv_project` (a directory to `uv run --project` `flyball-runner` from, when it isn't already on `flyballd`'s own `$PATH` -- same need as `flyball run`'s `--uv`) |
| `data_dir` | `data` | captured runner logs, under `logs/`: `NAME.log`, and `NAME.log.1` once it has been capped. A log can hold secrets (a `?token=` in a request line), so `logs/` is made `0700` and each file `0600`, also when they already exist |
| `log_max_size` | 10 MiB (`10485760`, in bytes) | per-runner captured-log cap; `0` for none. Checked every 2 s: past it, `NAME.log` is copied to `NAME.log.1` (replacing the last one) and emptied, so a runner's logs take at most about twice the cap. The runner keeps writing to the same file, so a daemon crash does not cut its output; a line written at the moment of the copy can be lost |
| `auth.token` | none | the bearer token every route of the daemon's own needs -- the runner list, `/`, and the commands below; only `GET /api/auth` is open. **With no token they answer 503**: the runners in `manifests_dir` still start and are proxied, but nothing can list, start, stop, restart or read one over the API |

A runner's `name` is lower-case letters, digits, `-` and `_` (it names the
log file and the URL prefix); `root_path` is `/segments` of the same; `host`
is loopback; `restart` is one of the three below. A manifest that says otherwise is refused, at start-up or over the API (400).

A runner's `status` (in `GET /api/runners`) follows its process:

| status | |
| --- | --- |
| `starting` | spawned, not yet answering `GET <root_path>/api/auth` (probed every 0.5 s) |
| `running` | answering |
| `restarting` | it exited, its `restart` policy restarts it, and it is waiting out the backoff: 1 s, doubling to 30 s, back to 1 s once a restart reaches `running` |
| `stopped` | it exited cleanly on its own, and its `restart` policy says not to restart it |
| `failed` | it crashed and its `restart` policy says not to restart it, it exited 2 (a bad rig file), or it could not be started again |

A manifest's `restart` says what happens when a runner exits on its own:

| `restart` | a crash (non-zero exit) | a clean exit (0) |
| --- | --- | --- |
| `on-failure` (default) | restarted, after the backoff | `stopped` |
| `always` | restarted, after the backoff | restarted, after the backoff |
| `never` | `failed` | `stopped` |

Exit code 2 is never restarted, under any policy: it is `flyball-runner`'s
code for a rig file that does not validate or a rig that cannot be built
(and `uv`'s, for a project or command it cannot find), which a restart
cannot fix. The runner is `failed` until `daemon restart`.

`daemon restart` restarts a runner at once, whatever its exit code, and
cuts short a crash backoff; a `stopped` or `failed` runner starts again. `daemon stop` sends `SIGTERM`, `SIGKILL`s a
runner still there after 10 s, and returns once it is gone -- also for a
runner in backoff, which does not come back.

### Daemon-managed commands (via `$FLYBALLD_URL`, never routed through a runner)

| command | | |
| --- | --- | --- |
| `daemon runners` | `GET /api/runners` | list registered runners and their status |
| `daemon start MANIFEST.json` | `POST /api/runners` | register and start one |
| `daemon stop NAME` | `DELETE /api/runners/NAME` | stop and deregister it |
| `daemon restart NAME` | `POST /api/runners/NAME/restart` | restart it |
| `logs NAME` | `GET /api/runners/NAME/logs` | its captured stdout/stderr (`NAME.log`; not the capped-off `NAME.log.1`) |

All of them send `Authorization: Bearer $FLYBALLD_TOKEN` -- the daemon's
token, not a runner's -- and are 401 without it; so does picking the one
registered runner when `-s` is omitted. The proxy under a runner's
`root_path` does not ask for it: the runner behind it has its own access
control. `GET /api/auth` says which you are: `level: read` anonymously,
`operate` with the token.

## Exit codes

| | |
| --- | --- |
| 0 | done |
| 1 | the rig, the daemon, or a local check refused; the message is theirs |
| 2 | no command given |
