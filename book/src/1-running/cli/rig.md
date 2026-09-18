# The rig and the runner

!!! tip "In the browser"
    [The Rig page](../ui/rig.md): the running document, changes, versions and restore, save, restart / shut down, connect a model; [Simulation](../ui/index.md#pages) for a simulated rig's knobs.

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
([Access](../runner/access.md#stopping-and-restarting-from-the-api)).
