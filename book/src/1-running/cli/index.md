# The CLI

`flyball` is a standalone Go binary (`daemon/cmd/flyball`), one client of
[the server](../../4-server/index.md) -- anything it can do the
[HTTP API](../../4-server/api.md) can too. Build it with `cd daemon &&
CGO_ENABLED=0 go build ./cmd/flyball`, or `./build-with-ui.sh` for one whose `flyball run`
serves the dashboard (no packaged install yet --
[Installing](../runner/index.md#installing)). Each page of this section is
one kind of task and names the UI page that does the same thing; the UI
pages point back here.

| page | commands | in the browser |
| --- | --- | --- |
| [Devices and signals](devices.md) | `status`, `devices`, `read`, `demand`, `watch`, `view`, `device-schema`, `invoke` | [Devices](../ui/devices.md) |
| [Controllers and tuning](controllers.md) | `controllers`, regulate / manual (via the API today), tunings | [Controllers](../ui/controllers.md), [Tuning](../autotune.md) |
| [Programs and activities](programs.md) | `program check / run / status / stop`, `activities`, `activity fire / cancel` | [Programs](../programs/writing.md#running-one) |
| [Sessions and export](sessions.md) | `sessions`, `export`, downloads by URL | [Sessions](../ui/sessions.md) |
| [The rig and the runner](rig.md) | `rig check / schema`, `sim …`, `stop`, `run`, save / versions / restart (via the API today) | [The Config page](../ui/rig.md), the **Software stop** button |
| [Without a rig](offline.md) | `rig check`, `rig schema`, `program schema`, `new` | -- |

It talks to one runner directly, or through a `flyballd` daemon by name --
see [addressing](../../7-reference/cli.md) for `-s`/`FLYBALL_URL`/
`FLYBALLD_URL`:

```
export FLYBALL_URL=http://pi:8000
flyball devices
flyball login                               # a password front: sign in once, a read token is saved
flyball login --scope operate               # ... or one that may drive this rig (30 days at most)
flyball --token T status                    # or export FLYBALL_TOKEN: a named token, or a bare runner's
flyball password                            # the hashed line for runner.front.password
```

Signing in, tokens and scopes: [the CLI reference](../../7-reference/cli.md#signing-in).

There is no schema caching, `--offline` mode, or per-device subcommand
tree built at start (those were the removed Python `cli.py`'s); a device's
own commands go through the fixed `invoke` subcommand instead.

## Output

Human-readable by default; most commands print raw JSON (`status` takes its
own `--json` for the same on that one command). Exit codes: 0; 1 for an
error the rig, the daemon or a local check reported.

## The client underneath

`flyball.interfaces.client.Rig` is the Python library the runner and the MCP server
build on internally; it's also usable standalone, imports nothing from the
rig, and is not what the Go CLI is built on (the CLI is a separate Go
implementation of the same HTTP calls):

```python
from flyball.interfaces.client import Rig

rig = Rig("http://pi:8000")
rig.devices.heater.set_limit(limit=0.5)
rig.devices.probe.view()["conditions"]
rig.write("heaters.heater1", 1200.0)
for frame in rig.watch("controllers"):
    ...
```

Arguments are validated against the command's schema before anything is
sent, so a wrong call fails locally with the schema's own words.
