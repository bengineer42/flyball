# Without a rig

!!! tip "In the browser"
    Nothing: these are for the person writing files and drivers, before there is a rig to open.


These work with no daemon reachable; they use the configs installed here:

| | |
| --- | --- |
| `flyball rig check FILE` | validate a rig file: drivers, links, names, controllers |
| `flyball rig schema` | the rig file's JSON Schema, for an editor (`#:schema` in TOML) |
| `flyball program schema` | the program file's JSON Schema |
| `flyball program check --local FILE` | validate a program file against the commands installed here |
| `flyball new NAME` | write `NAME.py`: a complete device driver with a tag, ready to edit |

## Offline

`flyball --offline schema.json --help` builds the whole tree from a saved
schema, so `--help` works with no rig. If the rig is unreachable and a cached
schema exists, the cache is used: stale is better than no `--help` at all.
