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
| `--host ADDR` | | `host` | bind address; default `127.0.0.1` |
| `--port N` | | `port` | default 8000 |
| `--root-path /PREFIX` | `FLYBALL_ROOT_PATH` | `root_path` | serve everything under a prefix |
| `--log-level LEVEL` | | `log_level` | uvicorn's; default `info` |
| `--token T` | `FLYBALL_TOKEN` | `token` | bearer token every request must carry |
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

Exit codes: 0 on a clean stop; 2 for a rig file that does not load (one
line on stderr, no traceback). A restart asked over the API replaces the
process with the same command line.

What each does in practice: [Starting a rig](../1-running/runner/index.md);
the keys with their meanings: [The runner section](../2-config/runner.md).
