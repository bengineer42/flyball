# Stress rigs

Config files that push the runner and the UI harder than any real rig would,
plus the ordinary two-loop and reverse-acting examples that belong in
[`../simulated/`](../simulated/README.md) instead. Nothing here is Python:
every file is `sim_*`/`fake_*` links read by the schema, routes, CLI and UI
exactly as a real bench would be.

| file | what it stresses | scale |
| --- | --- | --- |
| `plant.yaml` | "the works": everything at once | 41 published signals, 20 units, 17 controllers (P/PI/PID × none/affine/table), 3 writable devices with no controller, poll periods spread 0.2–5 s |
| `torrent.yaml` | throughput | 8 signals at 0.02–0.05 s, clock ×40 -- comfortably over 1000 samples/s |
| `zoo.yaml` | breadth of unit | 21 signals, one per unit the registry renders, 4 with a controller |
| `sparse.yaml` | the empty states | 1 device, 1 signal, nothing writable, no controllers, no programs |
| `bare.yaml` | the *really* empty state | loads with nothing declared at all -- `RigConfig` requires no `links`, `devices` or `controllers`, so a rig file can be just a name; kept as the one file that puts every UI list into its empty state at once |
| `chaos.yaml` | bad control | small/fast/coupled/laggy/noisy furnace; one law that winds up into the millions of watts, one heater clamped to 40 % of its power, one open-loop zone |
| `longrun.yaml` | history at scale | clock ×600: ten hours of rig time in about a minute of wall time |

Two ordinary examples live in `../simulated/` instead and are documented in
that README: `chiller.yaml` (reverse-acting: negative plant gain) and
`dual.yaml` (two independent controllers, two units, one rig).

Every file has the shape `../simulated/README.md` and `../furnace/README.md`
describe: `links:` (`sim_*`/`fake_*` plants and transports), `devices:`
(`sim_daq` reading plant ports as `[RP]` signals, `sim_drive` driving them
from demands) and `controllers:` keyed by the target signal's address. A
furnace is one `furnace` daq and one `heaters` drive on the `tube` link, so
its addresses are `furnace.zoneN` and `heaters.heaterN`; a bare plant's
drive is `<name>.drive`, a fraction of full, with an `affine` feedforward
carrying the plant's static inverse.

## Running one

```bash
cd engine
uv run flyball-runner ../examples/stress/plant.yaml --record
flyball program run ../examples/stress/programs/plant-firing.yaml   # cd daemon && CGO_ENABLED=0 go build ./cmd/flyball first
flyball rig check ../examples/stress/chaos.yaml
```

Every stress rig has at least one program in `programs/`, except `sparse.yaml`
and `bare.yaml`, which deliberately have none. `chaos.yaml`'s mid-run
disturbance (failing a thermocouple, kicking a heater) is a `command` step
in `chaos-run.yaml` on the `furnace` and `heaters` devices (`fail`/`restore`
with a `signal`, `disturb` with a `signal` and an `offset`), plus a `wait`
whose own `timeout` gives up before its `duration` -- see
[Programs, "Commands"](../../book/src/1-running/programs/index.md#commands)
for both. Run it on its own:

```bash
flyball program run ../examples/stress/programs/chaos-run.yaml
```

`scripts/chaos-disturb.sh` predates the `command` step and drives the same
routes from outside the program instead; kept for reference, but
`chaos-run.yaml` no longer needs it run alongside.
