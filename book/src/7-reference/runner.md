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
| `--token T` | `FLYBALL_TOKEN` | `auth.token` | the bare runner's token: a bearer token for the CLI, MCP clients and scripts, and what a person trades for a session (the link printed at start, or the login page) |
| `--token-file PATH` | | `auth.token` | read the token from a file (beats `FLYBALL_TOKEN`); unreadable or empty: a token no one knows, so nothing gets in |
| `--anonymous none\|read` | `FLYBALL_ANONYMOUS` | `auth.anonymous` | what a caller with no token and no session may do; default `none` |
| `--insecure-open` | `FLYBALL_INSECURE_OPEN=1` | none (per run only) | serve with no token on the `--host` asked for, beyond loopback, knowingly; default: `127.0.0.1` instead |
| `--front-dir DIR` | none | none | started by a front (`flyball run`, `flyballd`), never by hand: bind the endpoint `DIR` names and take only principals signed with its key; `--host`, `--port`, `--token` and `--anonymous` are then ignored ([The front and the runner](../6-internals/front.md)) |
| `--password P`, `--session D` | `FLYBALL_PASSWORD`, `FLYBALL_SESSION` | `auth.password`, `auth.session` | removed: accepted and ignored with a warning; a bare runner has no password login |
| `--no-mcp` | `FLYBALL_NO_MCP=1` | `mcp: false` | do not mount `/mcp` |
| `--compose` | | `compose` | let the API build up a hardware rig |
| `--allow-save` | | `allow_save` | let the API write rig files |
| `--allow-shutdown` | | `allow_shutdown` | let the API stop or restart the runner |
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
does not load or a rig that cannot be built (one line on stderr, no
traceback), and for an unknown flag; 3 when another runner
already runs this rig (it holds `<store>.lock`, or the front-dir's `runner.lock`; the message names it); 4 when `--front-dir` is unsafe or incomplete, before the rig's lock is taken or any hardware touched. A restart asked over the API replaces the
process with the same command line. `SIGUSR1` [stops the rig](../1-running/runner/access.md#stopping-the-rig) without ending the process.

What each does in practice: [Starting a rig](../1-running/runner/index.md);
the keys with their meanings: [The runner section](../2-config/runner.md).
