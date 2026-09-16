# The daemon

The daemon is a rig attached to the HTTP server, running in the foreground.
A rig that a [rig file](equipment.md#a-rig-file) can describe needs no
Python at all:

```
flyball-daemon rig.yaml                          # loopback, port 8000
flyball-daemon rig.yaml --host 0.0.0.0 --record  # reachable, with a session open
```

The file is validated first (`flyball rig check rig.yaml` does the same
without serving); a bad file is a one-line message and exit code 2. With
`recording: true` in the file, or `--record`, a session is opened in
`--store` (default `<rig>.sqlite` beside the rig file) before serving. On
shutdown the programmer is interrupted, the session closed and the polled
devices stopped.

## Building a rig while it runs

A rig file is one way to populate a rig; the API is the other, and a daemon
needs no file at all:

```
flyball-daemon --store lab.sqlite           # an empty rig, named `rig`
```

Then post the same things the file would say -- a link, a device with the
file's envelope, a controller -- one at a time or as one document:

```
curl -X POST localhost:8000/api/links -d '{"name": "t1", "tag": "sim_plant", "model": "lag", "tau_s": 2}'
curl -X POST localhost:8000/api/devices -d '{"name": "probe", "driver": "sim_daq", "poll_s": 0.5,
     "config": {"link": "t1", "ports": {"signal": {"port": "output", "quantity": "level", "unit": "1"}}}}'
curl -X POST localhost:8000/api/devices -d '{"name": "drive", "driver": "sim_drive",
     "config": {"link": "t1", "ports": {"u": "input"}}}'
curl -X POST localhost:8000/api/controllers -d '{"target": "drive.u", "source": "probe.signal", "law": {"tag": "P", "kp": 0.8}}'
curl -X POST localhost:8000/api/rig -d @lab.yaml.json     # or all of it at once
```

A device added this way is bound, polled and, if a session is open,
recorded from then on; `DELETE /api/devices/{name}` takes it off with
everything that hung off it (its poll, controllers on it, inputs bound
into it). `GET /api/rig/document` is the running rig as a file would build
it.

Every change is a **version** in the store: the rig as loaded (or started
bare), then a row per change with a reason -- `added device probe`,
`detached controller drive.u`, `restored 3`. A session records the version
it started on, so its readings always have their rig beside them.
`GET /api/rig/versions` lists them; `POST /api/rig/versions/{id}/restore`
makes the running rig that version again.

What was added does not survive a restart by itself -- the daemon starts
from what its command line says -- unless you keep it:

- `POST /api/rig/save` with no body writes what changed since this start
  to `<rig>.d/added.yaml` beside the first rig file, and the daemon loads
  that directory as one more overlay next time. Your own files are never
  rewritten; delete the overlay to undo.
- `POST /api/rig/save {"path": "lab.yaml"}` writes the whole running rig,
  flattened, to a file of your choosing: how a rig built up from nothing
  becomes a rig file. It refuses a file the rig was loaded from unless
  `"overwrite": true`.
- `flyball-daemon --resume` starts from the last change made through the
  API instead of the files, for the morning after.
- A simulated rig, or one started with no file, can always be built up. A
  hardware rig -- one with a real link -- refuses the building routes with
  409 unless the daemon runs with `--compose`: adding a device that owns a
  PWM channel while a controller runs is something to have decided on.
  Reading and saving are never refused.
- `--drivers DIR` (default `drivers/` beside the first rig file) is a
  directory of driver modules imported before serving and again on
  `POST /api/drivers/reload`, so a driver written on the spot -- by hand or
  by a [model](mcp.md) -- can be attached without a restart or a package.
  `GET /api/drivers` lists every tag the daemon can build. An edited file
  re-registers its tags; devices already built keep the class they were
  built with.

An application with hardware the file cannot describe writes its own entry
point around [serve][flyball.daemon.serve], which is all the command does
after building the rig. For the simulated oven it is ten lines:

```python
--8<-- "serve.py"
```

```
cd book/src/snippets
python serve.py
```

Then, from another shell:

```
flyball status
flyball devices
flyball heater
flyball controllers
```

## The token

`--token T` (or `FLYBALL_TOKEN=T`) makes every request to `/api`, `/ws` and
`/mcp` require `Authorization: Bearer T`; a websocket, or a plain `GET`
the browser navigates to (an export link), may pass `?token=T` instead,
since a browser cannot set headers on either -- a URL is logged where a
header is not, so the header is the form to use wherever it can be set.
Anything else is 401
with a `detail` (a socket is closed with code 4401). The CLI, the client
and `flyball-mcp` take `--token` or the same variable; the UI asks for it.
Without a token the daemon serves anyone who can reach the port -- fine on
loopback, not on `--host 0.0.0.0`, and not on a rig a model can drive.

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
| `/api/drivers`, `/api/drivers/reload`, `/api/probe`, `/api/links/{name}/query` | what the daemon can build, load the drivers directory again, what the board has, one raw exchange on a link |
| `/mcp/read`, `/mcp/author`, `/mcp/operate` | the rig for a model: [the MCP server](mcp.md) |
| `/api/waits` | what a program is waiting on; fire or interrupt one |
| `/api/programs` | check a program file, run one, see what is running |
| `/api/dashboards` | the UI's saved dashboards for this rig; `dashboards/*.json` beside the rig file are imported on start |
| `/api/events`, `/ws/events` | what has happened: a step failed, a device went offline |
| `/api/history` | sessions, series, ticks, events, spans, stored tunings |
| `/ws/samples` | every sample as it arrives, each demand's write record beside its readback, and the polling runs |
| `/ws/controllers`, `/ws/waits` | a snapshot on connect, then what changed |
| `/docs` | OpenAPI, from FastAPI |

Full list: [HTTP and websocket API](../6-reference/api.md).

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

## Supervision

Nothing forks or writes a PID file. Run it under systemd `Type=simple` (or
any supervisor that restarts a foreground process) and let uvicorn's
`--host`/`--port` decide where it listens. On shutdown the server stops the
rig's polled devices; anything else — putting a controller in manual,
closing a session — is the application's to do.

## Recording

The daemon that wants history attaches a store as well:

```python
from flyball.db import SqliteStore
from flyball.server import set_store

store = SqliteStore("rig.db")
set_store(store)
rig.start_recording(store)
```

History routes then read the store, never the rig, so they also work against
a database copied from another machine with no rig attached.
