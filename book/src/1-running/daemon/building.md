# Building a rig while it runs

A rig file is one way to populate a rig; the API is the other. This page is the composition API from the operator's side; the routes are [Composition](../../4-server/api.md#composition), the UI for it [The Rig page](../ui/rig.md).

A daemon needs no file at all:

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
`detached controller drive.u`. Each names its `parent`, the version it was
made from, and one is the `head`: where the running rig is. A session
records the version it started on, so its readings always have their rig
beside them. `GET /api/rig/versions` lists them;
`POST /api/rig/versions/{id}/restore` makes the running rig that version
again. A restore writes nothing -- the head moves to the version restored
-- and the next change is a new version whose parent is that one, so the
history is a tree of what was actually built on, never a copy of a copy.

What was added does not survive a restart by itself -- the daemon starts
from what its command line says -- unless you keep it:

- `POST /api/rig/save` with no body writes what changed since this start
  to `<rig>.d/added.yaml` beside the first rig file, and the daemon loads
  that directory as one more overlay next time. Your own files are never
  rewritten; delete the overlay to undo.
- `POST /api/rig/save {"path": "lab.yaml"}` writes the whole running rig,
  flattened, to a file of your choosing: how a rig built up from nothing
  becomes a rig file. It refuses a file the rig was loaded from unless
  `"overwrite": true`, and it is refused altogether (409) unless the daemon
  runs with `--allow-save`, as is `POST /api/sim/save`: a daemon anyone can
  reach should not be able to write where it is told. The overlay save
  needs no flag.
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
  by a [model](../../4-server/mcp.md) -- can be attached without a restart or a package.
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

## The daemon section

Everything above that is about the *process* -- where it listens, what the
API may do, where its files are -- can be written in the rig file under
`daemon:` instead of on the command line, or in a file of its own that
`extends` the rig. One file is then the whole invocation:

```yaml
# humidity.yaml
extends: [../humidity/rig.yaml, ../humidity/sim.yaml]
daemon:
  port: 8001
  root_path: /humidity
  allow_shutdown: true
  store_dir: stores          # stores/<rig name>.sqlite
```

```
flyball-daemon humidity.yaml
```

The keys are the flags: `host`, `port`, `log_level`, `token`, `compose`,
`mcp`, `root_path`, `allow_save`, `allow_shutdown`, and the places --
`store`, `store_dir`, `programs`, `tunings`, `drivers`. A flag on the
command line (or its environment variable) beats the file; a path in the
file is relative to the first rig file's directory, a path on the command
line to the shell. `store_dir` names the store after the rig
(`<dir>/<name>.sqlite`, the file's stem when the rig has no name), so
several daemons keep their stores in one place; `--store` still wins. The
section is not part of the rig: it is not in `/api/rig/document`, a
version, or a save. `GET /api/daemon` reports what was resolved, less the
token.
