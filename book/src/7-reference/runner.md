# `flyball-runner` reference

```
flyball-runner [RIG…] [flags]
```

`RIG…` are rig files, later overlaying earlier; none starts an empty rig
built up through the API. Every flag below that mirrors a `runner:` key is
*unset* by default, so the file's value stands; a flag given (or its
environment variable) wins. Paths given as flags are relative to the shell,
paths in the file to the first rig file.

| flag | env | `runner:` key | |
| --- | --- | --- | --- |
| `--host ADDR` | | `host` | bind address; default `127.0.0.1`. Beyond loopback an open runner serves on `127.0.0.1` instead, with a warning: give it a token, or `--insecure-open`. Ignored with `--front-dir` |
| `--port N` | | `port` | default 8000; ignored with `--front-dir` |
| `--root-path /PREFIX` | `FLYBALL_ROOT_PATH` | `root_path` | serve everything under a prefix |
| `--log-level LEVEL` | | `log_level` | uvicorn's; default `info`; request lines drop the query string |
| `--token T` | `FLYBALL_TOKEN` | `auth.token` | the bare runner's token: a bearer token for the CLI, MCP clients and scripts, and what a person trades for a session (the link printed at start, or the login page); shorter than 22 characters warns at start |
| `--token-file PATH` | | `auth.token` | read the token from a file (beats `FLYBALL_TOKEN`); unreadable or empty: a token no one knows, so nothing gets in |
| `--anonymous none\|read` | `FLYBALL_ANONYMOUS` | `auth.anonymous` | what a caller with no token and no session may do; default `none` |
| `--insecure-open` | `FLYBALL_INSECURE_OPEN=1` | none (per run only) | serve with no token on the `--host` asked for, beyond loopback, knowingly; it then answers an IP address, `localhost` or the machine's own name (`hostname`, `<hostname>.local`), never another DNS name; default: `127.0.0.1` instead |
| `--front-dir DIR` | none | none | started by a front (`flyball run`, `flyballd`), never by hand: bind the endpoint `DIR` names and take only principals signed with its key; `--host`, `--port`, `--token` and `--anonymous` are then ignored ([The front and the runner](../6-internals/front.md)) |
| `--password P`, `--session D` | `FLYBALL_PASSWORD`, `FLYBALL_SESSION` | `auth.password`, `auth.session` | removed: accepted and ignored with a warning; a bare runner has no password login |
| `--no-mcp` | `FLYBALL_NO_MCP=1` | `mcp: false` | do not mount `/mcp` |
| `--compose` | | `compose` | let the API change a hardware rig (each change restarts it) |
| `--allow-save` | | `allow_save` | let the API write rig files |
| `--allow-shutdown` | | `allow_shutdown` | let the API stop or restart the runner (a rig edit's own restart does not need it) |
| `--on-shutdown stop\|keep` | `FLYBALL_ON_SHUTDOWN` | `on_shutdown` | what shutting down (and a restart) does to outputs: `stop` (default) writes each device's resolved stop, best-effort, not latched; `keep` writes nothing, leaving outputs energised with no process watching them. Not `--keep`, which is the scratch record's window |
| `--store PATH` | | `store` | the SQLite store; default `<rig>.sqlite` beside the file |
| -- | | `store_dir` | `<dir>/<rig name>.sqlite` instead (file only) |
| `--programs DIR` | | `programs` | program files to import; default `programs/` |
| `--tunings DIR` | | `tunings` | control-law configs; default `tunings/` |
| `--drivers DIR` | | `drivers` | driver `.py` files; default `drivers/` |
| `--keep D` | `FLYBALL_KEEP` | `keep` | scratch record window; default `1h` |
| `--keep-size S` | `FLYBALL_KEEP_SIZE` | `keep_size` | scratch record cap; default `256MB` |
| `--retain D` | `FLYBALL_RETAIN` | `retain` | delete unpinned sessions this long after they end; `0` never |
| `--rotate D` | `FLYBALL_ROTATE` | `rotate` | continue a recording in a new session at this length; `0` never |
| `--max-store S` | `FLYBALL_MAX_STORE` | `max_store` | keep the store under this size; `0` no cap |
| `--record` | | (`recording:` at the rig's top level) | open a recording session on start |
| `--resume` | | -- | start from the store's head rig version instead of the files |
| `--set KEY=VALUE` | | -- | override a value after loading; repeatable |

Durations `D`: a number with `ns`/`us`/`ms`/`s`/`m`/`h`/`d`/`w`, bare is
seconds. Sizes `S`: `kB`/`MB`/`GB`/`TB`, `KiB`/`MiB`/`GiB`, bare is bytes.

Exit codes: 0 on a clean stop (Ctrl-C or SIGTERM); 2 for a rig file that
does not load or a rig that cannot be built; 3 when another runner already
runs this rig; 4 when `--front-dir` is unsafe or incomplete; 5 when it
could not serve (its socket or port not bound, its server not started).
[The exit codes](cli.md#exit-codes) has the whole table, `flyball`'s own
codes beside them. A restart asked over the API replaces the
process with the same command line; so does a rig edit's, with `--resume`
added for a runner with no rig file ([Building a rig while it
runs](../1-running/runner/building.md)). `SIGUSR1` [stops the rig](../1-running/runner/access.md#stopping-the-rig) without ending the process; a shutdown applies each device's
[resolved stop](../2-config/devices/index.md#stop-what-a-stop-writes)
unless `--on-shutdown keep`.

What each does in practice: [Starting a rig](../1-running/runner/index.md);
the keys with their meanings: [The runner section](../2-config/runner.md).
