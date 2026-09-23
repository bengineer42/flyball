# Starting a rig

A rig that a [rig file](../../2-config/index.md) can describe needs no
Python at all. The usual way to start one is `flyball run`, which starts
the rig's runner behind a front that serves the dashboard:

```
flyball run rig.yaml                  # the dashboard at http://127.0.0.1:8000/, no sign-in
```

That is the `local` shape: no password, no certificate, this machine only.
To reach it from another machine, give the front a shape with a sign-in
(`runner.front` in the rig file, [Access](access.md#shapes-who-gets-in)).
[With the dashboard: `flyball run`](#with-the-dashboard-flyball-run) has
the rest.

The runner on its own, `flyball-runner`, is a rig attached to the HTTP
server, running in the foreground, with a door of its own:

```
flyball-runner rig.yaml                          # loopback, port 8000
FLYBALL_TOKEN=… flyball-runner rig.yaml --host 0.0.0.0 --record  # reachable, with a session open
```

Reachable needs a token: an open runner (none) asked for any other address
still runs the rig but serves on `127.0.0.1` only, with a warning, and
answers only to the names `localhost`, `127.0.0.1` and `[::1]`, unless that
run says `--insecure-open` (or `FLYBALL_INSECURE_OPEN=1`), when it answers an
IP address, `localhost` or the machine's own name --
[the bare runner](access.md#the-bare-runner).

!!! warning "flyball is not a safety system"
    Its stop is a control function, not an emergency stop in the sense of
    IEC 60204-1 or ISO 13850. Put the protection outside flyball: thermal
    cut-outs, a hardware emergency stop that removes power, and wiring such
    that de-energised is safe. [Unattended runs](#unattended-runs) says what
    to check before leaving a rig alone.

The file is validated first (`flyball rig check rig.yaml` does the same
without serving); a bad file is a one-line message and exit code 2, and so
is a rig that validates but cannot be built -- a driver that refuses its
config, a device that is not there -- so a supervisor can tell a config to
fix from a crash. With
`recording: true` in the file, or `--record`, a session is opened in
`--store` (default `<rig>.sqlite` beside the rig file) before serving. On
shutdown -- Ctrl-C (SIGINT) or SIGTERM, which is how `flyball run`, `flyball
runners stop` and systemd stop it -- the programmer is interrupted, the session closed and the polled
devices stopped, and the runner exits 0. A read stuck in its driver is waited
on for 2 s at most (over all devices together), then abandoned with a
warning naming the device, so a hung read does not hold up the shutdown. Open
connections -- a dashboard's websocket, a download -- get 5 s to finish,
then are closed.

One runner per rig: before it imports a driver, opens a link or touches the
store, the runner takes an exclusive lock on `<store>.lock` beside the store
(`flock`, so it goes with the process however that ends). A second runner
for the same rig -- the same store, which by default means the same rig
file -- exits 3 at once, naming the process that holds it (its pid and
command line, with any `--token`/`--password` value masked; the file is
0600), and leaves the live one's session and hardware alone. Started by a
front (`--front-dir`), it first takes `runner.lock` in its front-dir (exit 3
if another runner holds it), and exits 4, before touching anything, if that
directory is unsafe or incomplete.

Every log line -- the runner's own, uvicorn's and its access log -- starts
with the local time and its offset (`2026-09-23T10:35:20+0100 INFO
flyball.runner: …`), so a log `flyballd` or systemd keeps can be matched
against readings and events. `--log-level` sets how much.

The rest of this section: [access](access.md) -- the front and its
shapes, sign-in, tokens, the bare runner, the software stop, a sub-path,
stopping and restarting from the API -- and
[building a rig while it runs](building.md) -- the composition API,
versions, saving, `--resume`. Every setting the runner takes, as a file
rather than flags: [The runner section](../../2-config/runner.md).

## With the dashboard: `flyball run`

`flyball run RIG-FILE` starts the rig's **front** -- the Go server that
serves the dashboard, signs people in and passes `/api`, `/ws` and `/mcp`
on -- and runs `flyball-runner` behind it. The runner listens only on a
socket the front made for it and serves only requests the front signed,
so the front's door is the only one. `flyball-runner` flags after the rig
file go to the runner.

```
flyball run rig.yaml                        # 127.0.0.1:8000, the local shape
flyball run rig.yaml --listen 0.0.0.0:8000  # needs a shape with a sign-in in runner.front
```

`--uv` runs `flyball-runner` via `uv run --project <rig file's directory>`
instead of a bare exec, for a rig whose application (`examples/humidity`,
`examples/furnace`) manages its own venv rather than putting
`flyball-runner` on `$PATH`. `--listen` and `--uv` have rig-file
equivalents under
[`runner.front`](../../2-config/runner.md#front-how-flyball-run-serves-the-rig),
so a deployment that always wants the same invocation -- a Pi that runs the
same command at every boot -- sets them once in the file; a flag given on
the command line always wins. A runner that crashes is started again; the
run ends with the runner. Closing the terminal does not end it, nor does
losing whatever reads its output (`flyball run rig.yaml | tee out.txt`,
and `tee` dies with the SSH session): the front and the runner keep running, their output also goes to a log file in the
state directory (`~/.local/state/flyball/front-<id>/run.log`), and a notice
at start says so. Ctrl-C or SIGTERM ends it; if the runner's shutdown
hangs, a second Ctrl-C hurries it and a third kills it, and `flyball run`
exits only after the runner has (a hurried or killed runner may leave its
recording not cleanly closed). `flyball stop` stops the rig and leaves it
running. Every flag:
[`flyball run`](../../7-reference/cli.md#flyball-run).

## Unattended runs

What happens to the outputs when something goes wrong is decided by the
hardware and its wiring, not by flyball:

- **Closing the terminal does not stop the rig.** `flyball run` keeps its
  rig running when the terminal or the SSH session goes; Ctrl-C, SIGTERM or
  `flyball stop` stop it.
- **A crash, `kill -9`, a power loss or a hung machine leaves each output
  at its last value.** Nothing runs to change it: a Raspberry Pi's sysfs
  PWM, for example, may keep its duty cycle with nothing left to drive it.
- **A closed loop with a failed sensor can drive its actuator to its
  limit.** Set output limits (`limits` on the writable signal) and decide
  how faults are handled before any unattended run.
- **Before the first overnight run**, on the real hardware, try a
  [software stop](access.md#stopping-the-rig), a killed runner (`kill -9`)
  and a power cut, and write down where each output ends up.
- **Run an unattended rig under `flyballd` and systemd**
  ([its unit](../../7-reference/cli.md#what-stopping-flyballd-does)), not in
  an SSH session.

## Installing

flyball runs on Linux and macOS today. Windows is planned, not yet
supported.

```
git clone git@github.com:bengineer42/flyball.git && cd flyball
cd engine && uv sync --all-extras          # flyball's runner, every driver extra, the dev tools
cd ../ui && npm install && npm run build       # the dashboard, to ui/apps/dashboard/dist
cd ../daemon && ./build-with-ui.sh            # the flyball CLI, a standalone Go binary, dashboard embedded
```

`uv run flyball-runner …` from `engine/` starts a rig. There's no packaged
install or release binary for the CLI yet -- the build above produces
`daemon/flyball`; put it on `PATH` or run it from `daemon/`. A rig on a Raspberry Pi also wants `extensions/linux/` (`flyball-linux`); an
application such as `examples/humidity` has its own `uv sync` and brings
its drivers with it. Extras per integration: [Integrations](../../5-integrations/index.md).

The `flyballd` daemon supervises several runners behind one front: the
same `daemon/` build produces it, and [the CLI reference](../../7-reference/cli.md#the-daemon)
says how it is configured and driven. Its runners outlive it: stopping or
restarting `flyballd` leaves every rig running, and the next `flyballd`
takes them over ([what stopping `flyballd` does](../../7-reference/cli.md#what-stopping-flyballd-does)).

A plain `go build` embeds a placeholder instead of the dashboard.
`./build-with-ui.sh` (in `daemon/`) builds the UI, copies it into the Go
tree and builds `flyball` with it; a `go build ./cmd/flyballd` after that
embeds it too. A front built without it answers every page with one saying
so, and the API still works.

## What it exposes

| | |
| --- | --- |
| `GET /api/health` | one look: uptime, devices, controllers, conditions, alarms, activities, recording |
| `GET /api/schema` | every device's config, signal and command schemas |
| `/api/devices` | each device's signal tree, schema, and a `POST` per command |
| `/api/read`, `/api/signals` | a signal's reading, a namespace's sample, or a device's samples; put a demand on a writable signal |
| `/api/controllers`, `/api/tunings`, `/api/clock` | the live rig |
| `/api/links`, `/api/devices` (`POST`, `DELETE`), `/api/rig` | build the rig up while it runs: see above |
| `/api/rig/document`, `/api/rig/changes`, `/api/rig/versions`, `/api/rig/save` | the running rig as a file, what changed, its versions, saving it |
| `/api/rig/schema`, `/api/rig/config`, `/api/rig/check` | the rig file's schema, the file as loaded, validate a document without building |
| `/api/drivers`, `/api/drivers/reload`, `/api/probe`, `/api/links/{name}/query` | what the runner can build, load the drivers directory again, what the board has, one raw exchange on a link |
| `/mcp/read`, `/mcp/author`, `/mcp/operate` | the rig for a model: [the MCP server](../../4-server/mcp.md) |
| `/api/activities` | what a program is waiting on; fire or cancel one |
| `/api/rig/stop` | the [software stop](access.md#stopping-the-rig): the program interrupted, every controller to manual |
| `/api/auth` | who the caller is and what the door takes (a bare runner's; behind a front, the front answers it) |
| `/api/programs` | check a program file, run one, see what is running |
| `/api/dashboards` | the UI's saved dashboards for this rig; `dashboards/*.toml`/`*.yaml`/`*.json` beside the rig file are imported on start |
| `/api/events`, `/ws/events` | what has happened: a step failed, a device went offline |
| `/api/history` | sessions, series, ticks, events, spans, stored tunings |
| `/ws/samples` | every sample as it arrives, each demand's write record beside its readback, and the polling runs |
| `/ws/controllers`, `/ws/activities` | a snapshot on connect, then what changed |
| `/docs` | OpenAPI in Swagger UI, served by the runner itself, so it works offline (`/openapi.json` is the document); not passed on by a front |

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

The body is `{"detail": "<the exception's message>"}`. The store's failures
land on the same rows: a write it refuses (`ConstraintError`) is 409, a store
it cannot reach (`StoreUnavailableError`) is 503, and anything else it raises
is a bug and answers 500.

## Recording

The runner that wants history attaches a store as well:

```python
from flyball.record import SqliteStore
from flyball.interfaces.server import set_store

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
any supervisor that restarts a foreground process): `flyball run` for one
rig, `flyballd` for several ([its example unit](../../7-reference/cli.md#what-stopping-flyballd-does)).
A bare runner decides where it listens with `--host`/`--port` (or the
`runner:` section). On shutdown the server stops the rig's polled devices;
anything else — putting a controller in manual, closing a session — is the
application's to do.
