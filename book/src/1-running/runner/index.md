# Starting a rig

The runner is a rig attached to the HTTP server, running in the foreground.
A rig that a [config file](../../2-config/index.md) can describe needs no
Python at all:

```
flyball-runner rig.yaml                          # loopback, port 8000
flyball-runner rig.yaml --host 0.0.0.0 --record  # reachable, with a session open
```

The file is validated first (`flyball rig check rig.yaml` does the same
without serving); a bad file is a one-line message and exit code 2. With
`recording: true` in the file, or `--record`, a session is opened in
`--store` (default `<rig>.sqlite` beside the rig file) before serving. On
shutdown the programmer is interrupted, the session closed and the polled
devices stopped.

The rest of this section: [access and safety](access.md) -- the door, a
sub-path behind a proxy, stopping and restarting from the API -- and
[building a rig while it runs](building.md) -- the composition API,
versions, saving, `--resume`. Every setting the runner takes, as a file
rather than flags: [The runner section](../../2-config/runner.md).

## Installing

```
git clone git@github.com:bengineer42/flyball.git && cd flyball
cd engine && uv sync --all-extras          # flyball's runner, every driver extra, the dev tools
cd ../ui && npm install && npm run build       # the dashboard, to ui/apps/dashboard/dist
cd ../daemon && go build ./cmd/flyball         # the flyball CLI, a standalone Go binary
```

`uv run flyball-runner …` from `engine/` starts a rig. There's no packaged
install or release binary for the CLI yet -- `daemon/cmd/flyball`'s build
above produces a `flyball` binary, put it on `PATH` or run it from
`daemon/`. A rig on a Raspberry Pi also wants `extensions/linux/` (`flyball-linux`); an
application such as `examples/humidity` has its own `uv sync` and brings
its drivers with it. Extras per integration: [Integrations](../../5-integrations/index.md).

The `flyballd` daemon supervises several runners behind one address: the
same `daemon/` build produces it, and [the CLI reference](../../7-reference/cli.md#the-daemon)
says how it is configured and driven.

## What it exposes

| | |
| --- | --- |
| `GET /api/health` | one look: uptime, devices, controllers, conditions, alarms, waits, recording |
| `GET /api/schema` | every device's config, signal and command schemas |
| `/api/devices` | each device's signal tree, schema, and a `POST` per command |
| `/api/read`, `/api/signals` | a signal's reading, a namespace's sample, or a device's samples; put a demand on a writable signal |
| `/api/controllers`, `/api/tunings`, `/api/clock` | the live rig |
| `/api/links`, `/api/devices` (`POST`, `DELETE`), `/api/rig` | build the rig up while it runs: see above |
| `/api/rig/document`, `/api/rig/changes`, `/api/rig/versions`, `/api/rig/save` | the running rig as a file, what changed, its versions, saving it |
| `/api/rig/schema`, `/api/rig/config`, `/api/rig/check` | the rig file's schema, the file as loaded, validate a document without building |
| `/api/drivers`, `/api/drivers/reload`, `/api/probe`, `/api/links/{name}/query` | what the runner can build, load the drivers directory again, what the board has, one raw exchange on a link |
| `/mcp/read`, `/mcp/author`, `/mcp/operate` | the rig for a model: [the MCP server](../../4-server/mcp.md) |
| `/api/waits` | what a program is waiting on; fire or interrupt one |
| `/api/programs` | check a program file, run one, see what is running |
| `/api/dashboards` | the UI's saved dashboards for this rig; `dashboards/*.toml`/`*.yaml`/`*.json` beside the rig file are imported on start |
| `/api/events`, `/ws/events` | what has happened: a step failed, a device went offline |
| `/api/history` | sessions, series, ticks, events, spans, stored tunings |
| `/ws/samples` | every sample as it arrives, each demand's write record beside its readback, and the polling runs |
| `/ws/controllers`, `/ws/waits` | a snapshot on connect, then what changed |
| `/docs` | OpenAPI, from FastAPI |

Full list: [HTTP and websocket API](../../4-server/api.md).

## Errors

The server maps flyball's error bases to status codes once, so no route
decides its own:

| raised | status | meaning for the caller |
| --- | --- | --- |
| `NotFoundError` | 404 | no such named thing |
| `ConflictError` | 409 | wrong state; stop or start something and retry |
| `UnachievableError`, `ValueError` | 422 | well formed, but the numbers cannot be applied |
| `NotReadyError` | 503 | rig not configured, or no reading yet |
| `HardwareError` | 503 | a device failed; usually transient |

The body is `{"detail": "<the exception's message>"}`.

## Recording

The runner that wants history attaches a store as well:

```python
from flyball.db import SqliteStore
from flyball.server import set_store

store = SqliteStore("rig.db")
set_store(store)
rig.start_recording(store)
```

History routes then read the store, never the rig, so they also work against
a database copied from another machine with no rig attached.

## The scratch record

While nobody is recording, the runner records everything anyway -- into a
session of its own, the *scratch record* (`kind: "scratch"` in the sessions
list) -- and keeps the last `keep` of it: an hour, unless the
[`runner:` section](../../2-config/runner.md) says otherwise. So a chart
opened on a rig nobody is recording still shows the last hour, and anything
worth keeping can be kept after the event: **Keep…** on the
[Sessions page](../ui/sessions.md) (`POST /api/history/sessions/{id}/keep`)
copies a range into a closed session of its own, and **include the last …**
when starting a recording (`include_ns`) starts the new session that far
back, filled from scratch.

Starting a recording replaces the scratch record; when the recording ends
a fresh one opens. `GET /api/recording` answers `null` while only scratch
runs: the rig is "not recording". The scratch record cannot be ended or
deleted over the API; it trims itself. One from an earlier run of the
runner stays until it ages out, so history survives a restart.

## What ages out

Every 30 seconds of wall time the runner sweeps the store:

- a recording longer than `rotate` is closed and continued in a new
  session (`continues` names the old one; its details carry over);
- unpinned sessions that ended more than `retain` ago are deleted;
- the scratch record is trimmed to `keep` and under `keep_size`;
- if the store is over `max_store`, the oldest data goes first whatever its
  kind -- a whole ended session or the oldest tenth of the scratch record,
  whichever is older -- never a pinned session, never the recording in
  progress.

Pin a session (`PUT /api/history/sessions/{id} {"pinned": true}`, or the
pin on the Sessions page) to exempt it. Windows are in the rig's clock: on
a simulation running at ×60, an hour of `keep` is a wall minute, and
between sweeps the record overshoots `keep` by up to 30 s × speed. Sizes
are bytes on disk; a SQLite file does not shrink when rows go -- freed
pages are reused -- so `max_store` measures the file less its free pages.
The keys, their spellings and defaults: [The runner section](../../2-config/runner.md).

## Supervision

Nothing forks or writes a PID file. Run it under systemd `Type=simple` (or
any supervisor that restarts a foreground process) and let `--host`/`--port`
(or the `runner:` section) decide where it listens. On shutdown the server stops the
rig's polled devices; anything else — putting a controller in manual,
closing a session — is the application's to do.
