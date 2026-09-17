# The rig and the daemon

!!! tip "In the browser"
    [The Rig page](../ui/rig.md): the running document, changes, versions and restore, save, restart / shut down, connect a model; [Simulation](../ui/index.md#pages) for a simulated rig's knobs.

| command | |
| --- | --- |
| `flyball rig check FILE…` | validate rig files (later overlays earlier) against the drivers installed *here*; prints the merged document. No daemon needed |
| `flyball rig schema` | the rig file's JSON Schema, for an editor |
| `flyball sim` | a simulated rig's clock and every plant's parameters (`GET /api/sim`) |
| `flyball sim clock N` | run the rig's time at N× (`PUT /api/sim/clock`) |
| `flyball sim set PLANT k=v …` | change a plant's parameters live (`PUT /api/sim/plants/{name}`) |
| `flyball sim reset PLANT` | put a plant back to a state (`POST /api/sim/plants/{name}/reset`) |
| `flyball sim config` / `sim save [PATH]` | the rig file as it now stands; write it back -- `save` needs the daemon started with `--allow-save` (`GET /api/sim/config`, `POST /api/sim/save`) |

Saving the running rig, its versions and restoring one, and stopping or
restarting the daemon have no subcommand yet; the routes are
[Composition](../../4-server/api.md#composition) and
[The daemon](../../4-server/api.md#the-daemon), and the last two answer 409
unless the daemon was started with `--allow-shutdown`
([Access](../daemon/access.md#stopping-and-restarting-from-the-api)).
