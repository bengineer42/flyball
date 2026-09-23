# The runner section

How the process serves: where it listens, what the API may do, where its
files are. Nothing here is about the equipment, so it is not part of the
rig document or a version, a save to a new file does not write it (a save
over an existing file keeps that file's section), and it may live in a file of its own
that `extends` the rig. Every key has a command-line flag of the same name;
a flag (or its environment variable) beats the file. A path in the file is
relative to the first rig file's directory.

| key | type | default | flag | |
| --- | --- | --- | --- | --- |
| `host` | string | `127.0.0.1` | `--host` | a [bare runner](../1-running/runner/access.md#the-bare-runner)'s bind address; loopback unless the rig should be reachable. Anything else needs `auth.token`: an open runner asked for it serves on `127.0.0.1` instead, with a warning, and answers only to `localhost`, `127.0.0.1` and `[::1]`, unless run with `--insecure-open` / `FLYBALL_INSECURE_OPEN=1` (a per-run switch; no key here). Ignored behind a front, which gives the runner a socket instead |
| `port` | int | `8000` | `--port` | a bare runner's port; ignored behind a front |
| `root_path` | `/prefix` | none | `--root-path`, `FLYBALL_ROOT_PATH` | serve everything under a path: `/furnace/api`, `/furnace/ws`, … for several bare runners on one origin -- [a sub-path](../1-running/runner/access.md#a-sub-path). `flyballd` sets it from the manifest's `root_path`. Under a front (`flyball run`, or `flyballd` without a manifest `root_path`) the rig file's own value is ignored, with a warning: the front decides where the rig is served, and `flyball run` serves it at `/` |
| `log_level` | string | `info` | `--log-level` | uvicorn's; its request lines keep the path and drop the query (`/api/export/1.csv?…`) |
| `front` | table | the `local` shape on `127.0.0.1:8000` | | how `flyball run`'s front serves the rig -- [below](#front-how-flyball-run-serves-the-rig) |
| `auth` | table | open (loopback names only) | | who may reach a *bare* runner, one with no front -- [the bare runner](../1-running/runner/access.md#the-bare-runner); the keys below. Ignored behind a front, with a warning |
| `auth.token` | string | none | `--token`, `--token-file PATH`, `FLYBALL_TOKEN` | the bare runner's one credential: a bearer token for machines, which a person trades for a session through the link the runner prints at start or at the login page (`runner.token` at the top level still parses) |
| `auth.anonymous` | `none` / `read` | `none` | `--anonymous`, `FLYBALL_ANONYMOUS` | what a caller with no token and no session may do: nothing, or every `GET` and stream |
| `auth.password`, `auth.session`, `auth.secret` | | | `--password`, `--session`, `FLYBALL_PASSWORD`, `FLYBALL_SESSION` | removed: read, so an old file still starts, and ignored with a warning. A password is the front's now (`front.password`) |
| `mcp` | bool | `true` | `--no-mcp`, `FLYBALL_NO_MCP` | mount the MCP servers at `/mcp/{read,author,operate}` |
| `compose` | bool | `false` | `--compose` | let the API add links and devices to a *hardware* rig; a simulated or bare rig always may |
| `allow_save` | bool | `false` | `--allow-save` | let the API write rig files: `/api/rig/save` to a path, `/api/sim/save`. The overlay save (`<rig>.d/added.yaml`) needs no flag |
| `allow_shutdown` | bool | `false` | `--allow-shutdown` | let the API stop or restart the runner (`/api/runner/shutdown`, `/restart`) |
| `store` | path | `<rig>.sqlite` beside the file | `--store` | the SQLite store: sessions, versions, programs, dashboards |
| `store_dir` | path | none | -- | put the store at `<store_dir>/<name>.sqlite` instead, so several runners keep theirs in one place; `store` wins |
| `programs` | path | `programs/` beside the file | `--programs` | program files imported into the library at start |
| `tunings` | path | `tunings/` beside the file | `--tunings` | control-law config files loaded onto `rig.tunings` |
| `drivers` | path | `drivers/` beside the file | `--drivers` | driver `.py` files imported at start and on `/api/drivers/reload` |
| `keep` | duration | `1h` | `--keep`, `FLYBALL_KEEP` | how much the [scratch record](../1-running/runner/index.md#the-scratch-record) holds while nothing is being recorded, in the rig's clock (`30m`, `2h`); `0` keeps none |
| `keep_size` | size | `256MB` | `--keep-size`, `FLYBALL_KEEP_SIZE` | the most the scratch record may take on disk; the oldest goes first |
| `retain` | duration | `0` (forever) | `--retain`, `FLYBALL_RETAIN` | delete an unpinned session this long after it ended (`30d`) |
| `rotate` | duration | `0` (never) | `--rotate`, `FLYBALL_ROTATE` | close a recording at this length and continue it in a new session (`24h`) |
| `max_store` | size | `0` (no cap) | `--max-store`, `FLYBALL_MAX_STORE` | keep the store under this size by deleting the oldest data of any kind, never a pinned session (`20GB`) |

A **duration** is a number with `ns`, `us`, `ms`, `s`, `m`, `h`, `d` or `w`
(a bare number is seconds); a **size** is `kB`/`MB`/`GB`/`TB` (decimal),
`KiB`/`MiB`/`GiB` (binary), or bare bytes. A bad spelling is refused when
the file loads. What the five retention keys do at run time -- sweeps,
what ages out, pins -- is [What ages out](../1-running/runner/index.md#what-ages-out).

`GET /api/runner` reports what was resolved, less `auth` and `front`, with
`endpoint` (`tcp:<host>:<port>`, or `unix:<path>` behind a front) for
`host` and `port` -- the five
retention keys both as written and resolved (`keep_ns`, `keep_bytes`,
`retain_ns`, `rotate_ns`, `max_bytes`; 0 = off). Command-line only:
`--record`, `--resume`, `--set KEY=VALUE`.

## `front:` -- how `flyball run` serves the rig

`runner.front` is read by `flyball run`'s front (the Go CLI), never by
`flyball-runner`: which [shape](../1-running/runner/access.md#shapes-who-gets-in)
the door has, where it listens, and its name and certificate. `flyballd`
reads the same keys from the top level of `flyballd.yaml`. Nothing set is
the `local` shape on `127.0.0.1:8000`: the dashboard, no sign-in, this
machine only.

```yaml
runner:
  front:
    listen: 0.0.0.0:8000          # --listen ADDR
    auth: password                # local (default) | password | proxy
    password: $scrypt$…           # flyball password
    anonymous: read               # anyone may watch
    url: https://pi.lab.example   # the address people use (optional)
    tls: {cert: /etc/flyball/tls/cert.pem, key: /etc/flyball/tls/key.pem}   # optional
    uv: true                      # --uv
```

| key | type | default | |
| --- | --- | --- | --- |
| `listen` | `host:port` or `unix:/path` | `127.0.0.1:8000` | where the front listens; `flyball run --listen ADDR` wins |
| `auth` | `local` / `password` / `proxy` | `local` | the shape. `local` serves loopback only; `sso` is not in this release and falls back |
| `password` | `$scrypt$` line | none | the admin password for `auth: password`, as `flyball password` prints it; a plain one is refused |
| `anonymous` | `none` / `read` | `none` | what a caller who has not signed in may do, under `password` and `proxy` |
| `url` | `http(s)://host[:port]` | none | the address people use: the names and origins the front accepts, and a `Secure` cookie when `https` |
| `tls` | `{cert, key}` | none | serve HTTPS from these PEM files, re-read when renewed |
| `proxy` | table | none | `auth: proxy`: the identity proxy in front -- `{preset: authelia}` and the like |
| `uv` | bool | `false` | run `flyball-runner` via `uv run --project <the rig file's directory>`, for an application with its own venv; `flyball run --uv` does the same |

`session`, `trusted_proxies`, `tokens` (lifetimes) and every `proxy` key
are reference material: [The front](../7-reference/rig-file.md#the-front).
A wrong value in this block never stops the rig: the front falls back to
the `local` shape on `127.0.0.1` and says why
([Access](../1-running/runner/access.md#when-a-setting-is-wrong)).

`runner.run`, this block's name before, is still read for one release,
with a warning: `serve_ui` as `listen`, `uv` as `uv`; its `port` means
nothing now, since the runner behind a front listens on a socket. When
both are set, `front` wins and `run` is ignored.

## A runner file

The section need not sit in the rig file. A file per deployment that
`extends` the rig and carries only `runner:` is the whole invocation --
`examples/site/humidity.yaml`:

```yaml
extends: [../humidity/rig-multi-sensor.yaml, ../humidity/sim.yaml]
runner:
  port: 8001
  root_path: /humidity
  allow_shutdown: true
  store_dir: stores          # stores/humidity-sim.sqlite
  auth:
    anonymous: read          # anyone may watch
    token: …                 # or FLYBALL_TOKEN; needed to drive
```

```
flyball-runner humidity.yaml
```

That is a bare runner, with its own token. The same file under `flyball
run` would carry a `front:` block instead, and `port`, `root_path` and
`auth` would be ignored. What each of these does at run time -- the door, a
sub-path behind a proxy, stopping and restarting, saving -- is in
[Starting a rig](../1-running/runner/index.md); what the API then answers, in
[The server](../4-server/index.md).

