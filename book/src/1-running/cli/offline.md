# Without a rig

!!! tip "In the browser"
    Nothing: these are for the person writing files and drivers, before there is a rig to open.


These work with no runner reachable; they use the configs installed here:

| | |
| --- | --- |
| `flyball rig check FILE` | validate a rig file: drivers, links, names, controllers |
| `flyball rig schema` | the rig file's JSON Schema, for an editor (`#:schema` in TOML) |
| `flyball program schema` | the program file's JSON Schema |
| `flyball new NAME` | write `NAME.py`: a complete device driver with a tag, ready to edit |

`program check` always validates against a running rig
(`POST /api/programs/check`) -- there is no offline,
against-the-commands-installed-here mode for program files the way there is
for rig files.
