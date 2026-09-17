# The daemon section

How the process serves: where it listens, what the API may do, where its
files are. Nothing here is about the equipment, so it is not part of the
rig document, a version or a save, and it may live in a file of its own
that `extends` the rig. Every key has a command-line flag of the same name;
a flag (or its environment variable) beats the file. A path in the file is
relative to the first rig file's directory.

| key | type | default | flag | |
| --- | --- | --- | --- | --- |
| `host` | string | `127.0.0.1` | `--host` | bind address; loopback unless the rig should be reachable |
| `port` | int | `8000` | `--port` | |
| `root_path` | `/prefix` | none | `--root-path`, `FLYBALL_ROOT_PATH` | serve everything under a path: `/furnace/api`, `/furnace/ws`, … for several rigs on one origin -- [a sub-path](../1-running/daemon/access.md#a-sub-path) |
| `log_level` | string | `info` | `--log-level` | uvicorn's |
| `auth` | table | open | | who may reach the daemon -- [the door](../1-running/daemon/access.md#the-door-a-password-a-token-or-open); the keys below |
| `auth.password` | string | none | `--password`, `FLYBALL_PASSWORD` | what the UI's login page takes: the plain text, or the `$scrypt$` line from `flyball password` |
| `auth.token` | string | none | `--token`, `FLYBALL_TOKEN` | bearer token for the CLI, MCP clients and scripts (`daemon.token` at the top level still parses) |
| `auth.anonymous` | `none` / `read` | `none` | `--anonymous`, `FLYBALL_ANONYMOUS` | what a caller with neither may do: nothing, or every `GET` and stream |
| `auth.session` | duration | `12h` | `--session`, `FLYBALL_SESSION` | how long a login lasts |
| `auth.secret` | string | a key file beside the store | | what signs sessions; set it to keep sessions across machines or without a store |
| `mcp` | bool | `true` | `--no-mcp`, `FLYBALL_NO_MCP` | mount the MCP servers at `/mcp/{read,author,operate}` |
| `compose` | bool | `false` | `--compose` | let the API add links and devices to a *hardware* rig; a simulated or bare rig always may |
| `allow_save` | bool | `false` | `--allow-save` | let the API write rig files: `/api/rig/save` to a path, `/api/sim/save`. The overlay save (`<rig>.d/added.yaml`) needs no flag |
| `allow_shutdown` | bool | `false` | `--allow-shutdown` | let the API stop or restart the daemon (`/api/daemon/shutdown`, `/restart`) |
| `store` | path | `<rig>.sqlite` beside the file | `--store` | the SQLite store: sessions, versions, programs, dashboards |
| `store_dir` | path | none | -- | put the store at `<store_dir>/<name>.sqlite` instead, so several daemons keep theirs in one place; `store` wins |
| `programs` | path | `programs/` beside the file | `--programs` | program files imported into the library at start |
| `tunings` | path | `tunings/` beside the file | `--tunings` | control-law config files loaded onto `rig.tunings` |
| `drivers` | path | `drivers/` beside the file | `--drivers` | driver `.py` files imported at start and on `/api/drivers/reload` |
| `keep` | duration | `1h` | `--keep`, `FLYBALL_KEEP` | how much the [scratch record](../1-running/daemon/index.md#the-scratch-record) holds while nothing is being recorded, in the rig's clock (`30m`, `2h`); `0` keeps none |
| `keep_size` | size | `256MB` | `--keep-size`, `FLYBALL_KEEP_SIZE` | the most the scratch record may take on disk; the oldest goes first |
| `retain` | duration | `0` (forever) | `--retain`, `FLYBALL_RETAIN` | delete an unpinned session this long after it ended (`30d`) |
| `rotate` | duration | `0` (never) | `--rotate`, `FLYBALL_ROTATE` | close a recording at this length and continue it in a new session (`24h`) |
| `max_store` | size | `0` (no cap) | `--max-store`, `FLYBALL_MAX_STORE` | keep the store under this size by deleting the oldest data of any kind, never a pinned session (`20GB`) |

A **duration** is a number with `ns`, `us`, `ms`, `s`, `m`, `h`, `d` or `w`
(a bare number is seconds); a **size** is `kB`/`MB`/`GB`/`TB` (decimal),
`KiB`/`MiB`/`GiB` (binary), or bare bytes. A bad spelling is refused when
the file loads. What the five retention keys do at run time -- sweeps,
what ages out, pins -- is [What ages out](../1-running/daemon/index.md#what-ages-out).

`GET /api/daemon` reports what was resolved, less `auth` -- the five
retention keys both as written and resolved (`keep_ns`, `keep_bytes`,
`retain_ns`, `rotate_ns`, `max_bytes`; 0 = off). Command-line only:
`--record`, `--resume`, `--set KEY=VALUE`.

## A daemon file

The section need not sit in the rig file. A file per deployment that
`extends` the rig and carries only `daemon:` is the whole invocation --
`examples/site/humidity.yaml`:

```yaml
extends: [../humidity/rig.yaml, ../humidity/sim.yaml]
daemon:
  port: 8001
  root_path: /humidity
  allow_shutdown: true
  store_dir: stores          # stores/humidity-sim.sqlite
  auth:
    anonymous: read          # anyone may watch
    password: $scrypt$…      # flyball password; needed to drive
```

```
flyball-daemon humidity.yaml
```

What each of these does at run time -- the door, a sub-path behind a
proxy, stopping and restarting, saving -- is in
[Starting a rig](../1-running/daemon/index.md); what the API then answers, in
[The server](../4-server/index.md).

