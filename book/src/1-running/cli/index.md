# The CLI

`flyball` is a command line built from the rig's schema: one client of
[the server](../../4-server/index.md), so anything it can do the
[HTTP API](../../4-server/api.md) can, and the flags it grows per device
come from the same place as the UI's forms
([where a device's options come from](../../2-config/devices/generated.md)).
Each page of this section is one kind of task and names the UI page that
does the same thing; the UI pages point back here.

| page | commands | in the browser |
| --- | --- | --- |
| [Devices and signals](devices.md) | `status`, `devices`, `read`, `demand`, `watch`, `flyball <device> …` | [Devices](../ui/devices.md) |
| [Controllers and tuning](controllers.md) | `controllers`, regulate / manual (via the API today), tunings | [Controllers](../ui/controllers.md), [Tuning](../autotune.md) |
| [Programs and waits](programs.md) | `program check / run / status / stop`, `waits`, `wait fire / interrupt` | [Programs](../programs/writing.md#running-one) |
| [Sessions and export](sessions.md) | `sessions`, `export`, downloads by URL | [Sessions](../ui/sessions.md) |
| [The rig and the runner](rig.md) | `rig check / schema`, `sim …`, save / versions / restart (via the API today) | [The Rig page](../ui/rig.md) |
| [Without a rig](offline.md) | `rig check`, `rig schema`, `program schema`, `new`, `--offline` | -- |

It It fetches
`GET /api/schema` once, caches it under `~/.cache/flyball`, and builds a
subcommand per device and per device command. Nothing about any particular
device is written into it.

```
flyball --url http://pi:8000 devices        # or export FLYBALL_URL
flyball --token T status                    # or export FLYBALL_TOKEN, for a runner started with one
flyball password                            # the hashed line for runner.auth.password
```

## Output

Human-readable by default; `--json` prints one JSON document per line, for
piping. Exit codes: 0; 1 for an error the rig reported; 3 if the rig was
unreachable.

## The client underneath

The CLI is `flyball.client.Rig` with argparse in front. The client is usable
on its own and imports nothing from the rig:

```python
from flyball.client import Rig

rig = Rig("http://pi:8000")
rig.devices.heater.set_limit(limit=0.5)
rig.devices.probe.view()["conditions"]
rig.demand("heaters.heater1", 1200.0)
for frame in rig.watch("controllers"):
    ...
```

Arguments are validated against the command's schema before anything is
sent, so a wrong call fails locally with the schema's own words.
