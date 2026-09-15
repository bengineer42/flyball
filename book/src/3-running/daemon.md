# The daemon

The daemon is a rig attached to the HTTP server, running in the foreground.
A rig that a [rig file](equipment.md#a-rig-file) can describe needs no
Python at all:

```
flyball-daemon rig.toml                          # loopback, port 8000
flyball-daemon rig.toml --host 0.0.0.0 --record  # reachable, with a session open
```

The file is validated first (`flyball rig check rig.toml` does the same
without serving); a bad file is a one-line message and exit code 2. With
`recording = true` in the file, or `--record`, a session is opened in
`--store` (default `flyball.sqlite`) before serving. On shutdown the
programmer is interrupted, the session closed and the polled readers stopped.

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
flyball actuators
flyball heater
flyball loops
```

## What it exposes

| | |
| --- | --- |
| `GET /api/health` | one look: uptime, readers, loop modes, conditions, pending signals, recording |
| `GET /api/schema` | every device's config, settings, state and command schemas |
| `/api/actuators`, `/api/readers` | each device's view, schema, and a `POST` per command |
| `/api/sources`, `/api/loops`, `/api/tunings`, `/api/clock` | the live rig |
| `/api/signals` | what a program is waiting on; fire or interrupt one |
| `/api/programs` | check a program file, run one, see what is running |
| `/api/events`, `/ws/events` | what has happened: a step failed, a reader went offline |
| `/api/history` | sessions, series, ticks, events, spans, stored tunings |
| `/ws/samples` | every sample as it arrives |
| `/ws/loops`, `/ws/actuators`, `/ws/readers`, `/ws/signals` | a snapshot on connect, then what changed |
| `/docs` | OpenAPI, from FastAPI |

Full list: [HTTP and websocket API](../6-reference/api.md).

## Errors

The server maps flyball's six error bases to status codes once, so no route
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
rig's polled readers; anything else — parking an actuator, closing a
session — is the application's to do.

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
