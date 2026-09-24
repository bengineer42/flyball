# Programs and activities

!!! tip "In the browser"
    [Programs](../ui/index.md#pages) is the library (check, run, upload, new) and the running program's steps and events; an activity shows as a chip in the app bar.

| command | |
| --- | --- |
| `flyball program check FILE` | the rig normalises and validates a program file; nothing runs (`POST /api/programs/check`) |
| `flyball program run FILE [--cancel]` | start it on the rig; `--cancel` cancels one already running first (`POST /api/programs/run`) |
| `flyball program status` | where it is: the step, its events (`GET /api/programs/running`) |
| `flyball program cancel` | cancel it: it ends `cancelled`, outputs kept (`POST /api/programs/cancel`) |
| `flyball activities` | what the rig is waiting on, by name (`GET /api/activities`) |
| `flyball activity fire NAME` / `activity cancel NAME` | answer an activity as met, or cancel it (`POST /api/activities/{name}/fire` / `cancel`) |

Writing the file: [Writing and running programs](../programs/writing.md);
the steps and modifiers: [program file schema](../../7-reference/program-schema.md).
