# Domain scenario rigs

Config-only example rigs for a handful of top-ranked application domains.
Each is a real
`rig.yaml` (real hardware addresses and drivers) plus a `sim.yaml` overlay
(same addresses, no hardware) plus a `programs/` directory — the same shape
as `examples/humidity/`. To go from sim to real, drop the `sim.yaml` file
from the command line:

```bash
flyball-runner rig.yaml sim.yaml     # no hardware
flyball-runner rig.yaml              # the real skid/room
```

| directory | domain | signals | new drivers needed | example available |
| --- | --- | --- | --- | --- |
| `mushroom-room/` | mushroom/fungiculture grow room | humidity + CO2, two independent loops | none (`sht4x_set`, `scd30`, both already in `flyball_chips`) | yes, `rig.yaml`/`sim.yaml`/`programs/cultivation.yaml` |
| `aging-room/` | cheese/charcuterie/wine curing room | humidity only, temperature monitored | none (`sht4x_set`) | yes, `rig.yaml`/`sim.yaml`/`programs/wheel.yaml` |
| `dosing-skid/` | small-scale non-safety-critical dosing | pH/EC/ORP/DO probes + a capped dosing pump, no continuous control loop | none (`ezo_ph`/`ezo_ec`/`ezo_orp`/`ezo_do` in `flyball_chips`, `dosing_pump` in `flyball_linux`) | yes, `rig.yaml`/`sim.yaml` (no program yet — see below) |

## What's simplified here

- **Mushroom room's two loops don't couple.** A real grow tent's humidity and
  CO2 interact (more fresh-air exchange lowers both); the `sim.yaml` overlay
  uses two independent generic `sim_plant` links, not a bespoke coupled
  physics model like `examples/humidity/sim.yaml`'s `sim_humidity_chamber`.
  Good enough to try the program and the two loops in isolation; not a
  faithful simulation of the coupling.
- **Aging room's temperature signal is unmodelled** in the sim overlay — it
  mirrors the same plant's `humidity` output so the address exists, not a
  real second variable.
- **Dosing skid has no program yet.** Dosing is a discrete, capped
  `dispense()` command (see `dosing_pump.py`'s own docstring), not a
  continuous PI loop, so there's no `controllers:` entry to drive from a
  `regulate`/`ramp` program the way the other two scenarios do. A real
  demo program would need threshold logic (`wait` on a reading, then a
  `command: { device: doser, device_command: dispense, args: { volume_ml: … } }`)
  — not written here.
- **`dosing-skid/sim.yaml`'s probe readings are constant**, not scripted
  drift — `fake_uart`'s `replies` is a fixed script, and a single reply
  repeats forever, unlike `sim_plant`'s continuous model.

## What's not here

Other candidate domains (craft brewing/fermentation, greenhouse,
teaching-lab benches) are either config-only but not yet scoped into a rig
file, or already covered structurally by an existing example under a
different name.
