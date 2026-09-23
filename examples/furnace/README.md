# The furnace

A three-zone tube furnace: the complicated simulated rig. No hardware --
it needs only `flyball[server]` (for `flyball-runner`) and `flyball-sim`
-- kept as its own package because it registers `sim_furnace`, a worked
[`MultiPlant`][flyball_sim.plant.MultiPlant] example whose ports know their
own quantity, rather than the generic `sim_plant`/`sim_daq`/`sim_drive`
`examples/simulated/` uses.

`rig.yaml` is a `sim_furnace` link (`tube`) with two devices on it:
`furnace`, a `sim_daq` whose signals `furnace.zone1..3` and
`furnace.sample` are the plant's thermocouples, and `heaters`, a
`sim_drive` whose signals `heaters.heater1..3` are its heaters, each mapped
to a port of the one plant in the device's `ports`. The heaters are plain:
a demand is a power in watts, clamped to the zone's `power_w`, and the
middle zone's controller carries a `table` feedforward -- the single-zone
losses curve, as one would measure it at commissioning, plus a `rate_gain`
for the ramp -- so the law only has to cover what the neighbours add. The
zones conduct heat to each other, so holding the middle at 900 °C drags
the ends above their 600 °C setpoints with their heaters off — a heater
cannot cool — and losses are convective plus radiative, so the drive a
zone needs for the same step is several times larger at 900 than at 200.

The two device names are the point of the shape: a real furnace's file
would declare `furnace` (a thermocouple DAQ) and `heaters` (an SSR bank)
with the same addresses, and a `sim.yaml` overlay would swap only the
drivers for these (see the book, *Writing a sensor* / *Writing an
actuator*, `book/src/3-extending/device/{sensor,actuator}.md`).

Five programs in `programs/`, each a different lesson:

| program | furnace time | shows |
| --- | --- | --- |
| `firing.yaml` | 2.5 h | all three to 600, the middle to 900 — the ends get dragged above their setpoints with heaters off |
| `gradient.yaml` | 4 h | 800 / 600 / 400 along the tube — conduction makes the hot end saturate and the cold end's heater idle |
| `anneal.yaml` | 8 h | a driven 2 °C/min cooldown — the drive falls smoothly until the programmed rate exceeds the natural one |
| `step-test.yaml` | 5 h | identification steps on zone 2 at 300 and at 700 — the same 30 °C step, a different response: the case for a gain schedule |
| `load-sample.yaml` | a few minutes | the operator in the loop: two `wait` steps with a timeout, for trying the go button |

```bash
uv run flyball-runner rig.yaml                # clock at 60x from the file
flyball program run programs/firing.yaml      # cd daemon && go build ./cmd/flyball first
flyball program status
flyball sim show                              # the tube's parameters
flyball sim set tube coupling_w_per_k=20      # couple the zones harder, live
flyball invoke furnace fail signal=zone3      # open-circuit a thermocouple
flyball watch controllers
```

`flyball program run programs/firing.yaml` starts a firing; `flyball
program status` says where it is; `flyball wait fire wait` answers the
operator prompt at the end. With `clock: { stepped: true }` instead of a
speed, the same firing runs to completion in the time the arithmetic takes
— every poll, tick and hold in order — which is how
`tests/test_plant.py` tests it.

## Editing the file

The `# yaml-language-server: $schema=rig.schema.json` line at the top of
`rig.yaml` points a YAML editor (the VS Code YAML extension) at the rig
file's schema for completion and checking. The schema is generated from
the installed configs:

```bash
flyball rig schema > rig.schema.json
flyball rig check rig.yaml        # the same check, from the shell
```

## Config-only

Nothing in `rig.yaml` is Python; `sim_furnace` is registered by this
package's own `src/furnace/sim.py`, importing the generic `sim_daq`/
`sim_drive` machinery from `flyball_sim.devices` the way any other
`MultiPlant` link could. `../simulated/README.md` covers the generic
`sim_plant`/`sim_daq`/`sim_drive` shape this furnace builds on.
