# Building a rig while it runs

A rig file is one way to populate a rig; the API is the other. This page is the composition API from the operator's side; the routes are [Composition](../../4-server/api.md#composition), the UI for it [The Config page](../ui/rig.md). Every change is saved and the rig restarted with it (D-051): nothing is changed in place.

A runner needs no file at all:

```
flyball-runner --store lab.sqlite           # an empty rig, named `rig`
```

Then post the same things the file would say -- a link, a device with the
file's envelope -- one at a time or as one document:

```
curl -X POST localhost:8000/api/links -d '{"name": "t1", "type": "sim_plant", "model": "lag", "tau_s": 2}'
curl -X POST localhost:8000/api/devices -d '{"name": "probe", "driver": "sim_daq", "poll_s": 0.5,
     "link": "t1", "ports": {"signal": {"port": "output", "quantity": "level", "unit": "1"}}}'
curl -X POST localhost:8000/api/rig -d @lab.yaml.json     # or all of it at once
```

**Every change restarts the rig.** Nothing is added to, or taken off, the
running rig in place. A change -- a link or a device added or removed, a
document added, a version restored -- goes the same four steps:

1. **Checked.** The rig as it would be is validated whole: the types, the
   links devices name, and that each input and controller address is on a
   device of the rig. Refused, nothing changes and nothing stops (the answer
   says why: 422, 404 or 409).
2. **Saved,** as a new rig version (below) and, for a rig from files, in
   the overlay `<rig>.d/added.yaml` beside the first file, which every start
   loads after your files. The overlay holds only what differs from your
   files (a removal is a `null`), and is checked before it is written to
   build exactly that version. The overlay it replaces is kept as
   `added.yaml.prev`. A runner started with no file, or with `--resume`,
   keeps the change in the store alone.
3. **Stopped,** as `POST /api/rig/stop` stops it: a program running is
   cancelled, outputs go to their stop states, controllers go to manual.
4. **Restarted:** the runner replaces its own process, with the same
   command line (a bare or resumed one with `--resume` added), and builds
   the version saved. It comes up passive: every controller in manual,
   each driver at its build values, nothing written until someone acts. A
   recording in progress goes on in a new session.

The request answers 202 before the restart, with the version it saved
(`RigEditOut`); the API answers again a moment later. A label-only change
restarts too: the rig file is the rig, and a rebuild is the one way to make
the running rig match it. While a program runs, a change is refused unless
it says `?force=true`; `?base=<version>` refuses it if the rig moved on
from the version you read. If the version saved does not build (a device
that is not there), the runner puts the one before back, starts again on
it, and holds an `edit_not_built` condition on the rig saying why.

This needs no `--allow-shutdown`: that flag is for `POST /api/runner/restart`
and `/shutdown`. It works the same under `flyball run`, under flyballd, or
for a `flyball-runner` started by hand: the process restarts itself.

A controller is the one thing still attached in place
(`POST /api/controllers`); `DELETE /api/devices/{name}` removes the
controllers on the device with it. `GET /api/rig/document` is the running
rig as a file would build it.

Every change is a **version** in the store: the rig as loaded (or started
bare), then a row per change with a reason -- `edited: added device probe`,
`restored from 3`, `attached controller drive.u`. Each names its `parent`,
the version it was made from, and one is the `head`: where the running rig
is. A session records the version it started on, so its readings always
have their rig beside them. `GET /api/rig/versions` lists them;
`POST /api/rig/versions/{id}/restore` makes the rig that version again: a
new version on top, `restored from {id}`, built whole at the restart.

What the overlay holds is yours to keep or drop:

- Your rig file stays the starting document. A change you make to it later
  is taken at the next start wherever the overlay does not say otherwise;
  where it does, the overlay wins, and the runner's log says so at start
  (it lists what the overlay sets, and warns when a rig file changed after
  the overlay touches the same keys). Delete the overlay to go back to your
  files alone.
- `POST /api/rig/save` with no body writes to the same overlay what
  differs from your files and was not saved yet -- today, a controller
  attached or removed since the start. A save with nothing new leaves the
  file alone and says so (`written: false`). Your own files are never
  rewritten.
- `POST /api/rig/save {"path": "lab.yaml"}` writes the whole running rig,
  flattened, to a file of your choosing: how a rig built up from nothing
  becomes a rig file. It refuses a file the rig was loaded from unless
  `"overwrite": true` -- which then clears the overlay (kept as
  `added.yaml.prev`), since the file now holds it all -- and it is refused
  altogether (409) unless the runner runs with `--allow-save`, as is
  `POST /api/sim/save`: a runner anyone can reach should not be able to
  write where it is told. Saving over a file that exists keeps that file's
  `runner:` section (its `extends` resolved, so a base's section comes
  along into the flattened file): the rig's document has no runner
  section, and dropping it would drop `runner.auth` with it. The section
  is written to the file but not returned. A file whose section cannot be
  read is not overwritten (409).
- `flyball-runner --resume` starts from the store's last change instead of
  the files.
- A simulated rig, or one started with no file, can always be changed. A
  hardware rig -- one with a real link -- refuses the changes with 409
  unless the runner runs with `--compose`: a restart stops every output.
  Reading and saving are never refused.
- `--drivers DIR` (default `drivers/` beside the first rig file) is a
  directory of driver modules imported before serving and again on
  `POST /api/drivers/reload`, so a driver written on the spot -- by hand or
  by a [model](../../4-server/mcp.md) -- can be attached without a restart or a package.
  `GET /api/drivers` lists every type the runner can build. An edited file
  re-registers its tags; devices already built keep the class they were
  built with.

An application with hardware the file cannot describe writes its own entry
point around [serve][flyball.runner.serving.serve], which is all the command does
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
flyball view heater
flyball controllers
```

## The runner section

Everything above that is about the *process* -- where it listens, what the
API may do, where its files are -- can be written in the rig file under
`runner:` instead of on the command line, or in a file of its own that
`extends` the rig. One file is then the whole invocation:

```yaml
# humidity.yaml
extends: [../humidity/rig-multi-sensor.yaml, ../humidity/sim.yaml]
runner:
  port: 8001
  root_path: /humidity
  allow_shutdown: true
  store_dir: stores          # stores/<rig name>.sqlite
```

```
flyball-runner humidity.yaml
```

The keys are the flags: `host`, `port`, `log_level`, `auth` (password,
token, anonymous, session), `compose`, `mcp`, `root_path`, `allow_save`,
`allow_shutdown`, and the places --
`store`, `store_dir`, `programs`, `tunings`, `drivers`. A flag on the
command line (or its environment variable) beats the file; a path in the
file is relative to the first rig file's directory, a path on the command
line to the shell. `store_dir` names the store after the rig
(`<dir>/<name>.sqlite`, the file's stem when the rig has no name), so
several runners keep their stores in one place; `--store` still wins. The
section is not part of the rig: it is not in `/api/rig/document`, a
version, or a save. `GET /api/runner` reports what was resolved, less
`auth`.
