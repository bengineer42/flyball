# The rig and the runner

!!! tip "In the browser"
    [The Config page](../ui/rig.md): the running document, changes, versions and restore, save, restart / shut down, connect a model; [Simulation](../ui/index.md#pages) for a simulated rig's knobs.

| command | |
| --- | --- |
| `flyball rig check FILE… [--set KEY=VALUE] [--print]` | validate rig files (later overlays earlier) against the schema built into the binary -- `flyball`'s own drivers, not extras such as `flyball-linux`, whose tags it does not know (start the runner to check those); `--print` prints the merged document. No runner needed |
| `flyball rig schema` | the rig file's JSON Schema, for an editor |
| `flyball sim` | a simulated rig's clock and every plant's parameters (`GET /api/sim`) |
| `flyball sim clock N` | run the rig's time at N× (`PUT /api/sim/clock`) |
| `flyball sim set PLANT k=v …` | change a plant's parameters live (`PUT /api/sim/plants/{name}`) |
| `flyball sim reset PLANT` | put a plant back to a state (`POST /api/sim/plants/{name}/reset`) |
| `flyball sim config` / `sim save [PATH]` | the rig file as it now stands; write it back -- `save` needs the runner started with `--allow-save` (`GET /api/sim/config`, `POST /api/sim/save`) |

!!! note "`--print`'s formatting"
    The merged document prints with keys in alphabetical order (not the
    rig file's own order), whole-number floats without a trailing `.0`,
    and schema-defaulted fields left out when absent from the input files
    -- same data as the file(s) describe, not a byte-identical dump.

Saving the running rig, its versions and restoring one, and stopping or
restarting the runner have no subcommand yet; the routes are
[Composition](../../4-server/api.md#composition) and
[The runner](../../4-server/api.md#the-runner), and the last two answer 409
unless the runner was started with `--allow-shutdown`
([Access](../runner/access.md#stopping-and-restarting-the-runner-from-the-api)).

## Stopping the rig

```
flyball stop --reason "door open"           # the software stop: program interrupted, controllers to manual
flyball -s furnace stop                     # one rig behind flyballd
flyball stop --all                          # every rig flyballd runs that this credential may operate
flyball stop --front-dir /run/flyball/furnace   # on the rig's host: SIGUSR1 to the runner, no HTTP
flyball stop furnace.yaml                   # the same, for a rig started with `flyball run furnace.yaml`
```

Over HTTP it needs `operate`, prints what happened to each device, and in
this release writes nothing to any device: outputs are left as they were
([the software stop](../runner/access.md#stopping-the-rig)). The
`--front-dir` and rig-file forms signal the runner instead, which works
with no front, no credential and no network; the report goes to the
runner's log, or the run's `run.log`
([`flyball stop`](../../7-reference/cli.md#stopping-a-rig)).

## Starting one: `flyball run`

The one command here that needs no runner already up -- it starts one.
`flyball run RIG-FILE` runs `flyball-runner` behind a front that serves the
built dashboard on `127.0.0.1:8000` and passes `/api`, `/ws` and `/mcp` to
it: [Starting a rig](../runner/index.md#with-the-dashboard-flyball-run).
