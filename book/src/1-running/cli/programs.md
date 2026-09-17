# Programs and waits

!!! tip "In the browser"
    [Programs](../ui/index.md#pages) is the library (check, run, upload, new) and the running program's steps and events; a wait shows as a chip in the app bar.

| command | |
| --- | --- |
| `flyball program check FILE` | the rig normalises and validates a program file; nothing runs (`POST /api/programs/check`) |
| `flyball program run FILE [--interrupt]` | start it on the rig; `--interrupt` stops one already running first (`POST /api/programs/run`) |
| `flyball program status` | where it is: the step, its events (`GET /api/programs/running`) |
| `flyball program stop` | interrupt it (`POST /api/programs/interrupt`) |
| `flyball waits` | what the rig is waiting on, by name (`GET /api/waits`) |
| `flyball wait fire NAME` / `wait interrupt NAME` | answer a wait as met, or cancel it (`POST /api/waits/{name}/fire` / `interrupt`) |

Writing the file: [Writing and running programs](../programs/writing.md);
the steps and modifiers: [program file schema](../../7-reference/program-schema.md).
