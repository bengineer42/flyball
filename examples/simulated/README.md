# Simulated rigs

Rigs with nothing plugged in, written as files. Each one runs the real
runtime, server, recorder and CLI; only the devices are pretend. They are
here to try the system, to test against, and to show what a rig file is.

| file | plant | what it shows |
| --- | --- | --- |
| `oven.yaml` | first-order lag with dead time, noisy | the classic control problem; the autotune case |
| `tank.yaml` | integrator with a drain | a plant with no natural rest; why integral action matters |
| `bench.yaml` | scripted SCPI supply and meter over `fake_text` links | the exact file shape a real bench uses — change two link tags and it is hardware |
| `furnace.yaml` | three heated zones in a row, a sample, thermocouples that lag; radiative losses | the complicated one: controllers that interact, a gain that changes with temperature, a sensor you can fail, a two-hour firing at 60× |
| `chiller.yaml` | a lag with a *negative* gain | reverse-acting: the drive cools, so the plant rests at ambient and falls as the drive rises |
| `dual.yaml` | an oven (°C) and a tank (L), independent | one rig file is not one plant: two controllers in different units, neither touching the other |

Rigs built to break things on purpose — many more channels, absurd sample
rates, every unit the registry knows, deliberately bad tuning — live in
[`../stress/`](../stress/README.md).

## The furnace

`furnace.yaml` is a `sim_furnace` link with two devices on it: `furnace`, a
`sim_daq` whose signals `furnace.zone1..3` and `furnace.sample` are the
plant's thermocouples, and `heaters`, a `sim_drive` whose signals
`heaters.heater1..3` are its heaters, each mapped to a port of the one plant
in the device's `ports`. The heaters are plain: a demand is a power in
watts, clamped to the zone's `power_w`, and the middle zone's controller
carries a `table` feedforward -- the single-zone losses curve, as one would
measure it at commissioning, plus a `rate_gain` for the ramp -- so the law
only has to cover what the neighbours add. The zones conduct heat to each
other, so holding the middle at 900 °C drags the ends above their 600 °C
setpoints with their heaters off — a heater cannot cool — and losses are
convective plus radiative, so the drive a zone needs for the same step is
several times larger at 900 than at 200.

The two device names are the point of the shape: a real furnace's file would
declare `furnace` (a thermocouple DAQ) and `heaters` (an SSR bank) with the
same addresses, and a `sim.yaml` overlay would swap only the drivers for
these (`temp-docs/DEVICE-MODEL-PLAN.md` §1.6, §2).

Five programs in `programs/`, each a different lesson:

| program | furnace time | shows |
| --- | --- | --- |
| `firing.yaml` | 2.5 h | all three to 600, the middle to 900 — the ends get dragged above their setpoints with heaters off |
| `gradient.yaml` | 4 h | 800 / 600 / 400 along the tube — conduction makes the hot end saturate and the cold end's heater idle |
| `anneal.yaml` | 8 h | a driven 2 °C/min cooldown — the drive falls smoothly until the programmed rate exceeds the natural one |
| `step-test.yaml` | 5 h | identification steps on zone 2 at 300 and at 700 — the same 30 °C step, a different response: the case for a gain schedule |
| `load-sample.yaml` | a few minutes | the operator in the loop: two `wait` steps with a timeout, for trying the go button |

```bash
uv run flyball-daemon ../examples/simulated/furnace.yaml      # clock at 60x from the file
uv run flyball program run ../examples/simulated/programs/firing.yaml
uv run flyball program status
uv run flyball sim                                              # the tube's parameters
uv run flyball sim set tube coupling_w_per_k=20                 # couple the zones harder, live
uv run flyball furnace fail --signal zone3                      # open-circuit a thermocouple
uv run flyball watch controllers
```

`flyball program run ../examples/simulated/programs/firing.yaml` starts a
firing; `flyball program status` says where it is; `flyball signal fire wait`
answers the operator prompt at the end. With `clock: { stepped: true }` instead of a speed, the same firing runs to
completion in the time the arithmetic takes — every poll, tick and hold in
order — which is how `tests/test_furnace.py` tests it.

## Run one

```bash
cd controller
uv run python ../examples/simulated/demo.py ../examples/simulated/oven.yaml 50 600
```

steps the rig's clock through ten minutes and prints the controller settling.
For the daemon and everything on top of it:

```bash
uv run flyball-daemon ../examples/simulated/oven.yaml --record
flyball status                   # devices, controllers, waits at a glance
flyball sim clock 60             # a simulated minute per second; `flyball sim` for the knobs
flyball heater disturb --signal drive --offset -0.3    # open the door
flyball watch controllers
```

## The file shape

A rig file is `links:` (transports, or here simulated plants), `devices:`
keyed by name, and `controllers:` keyed by the address of the signal each
one drives. A device entry is the envelope every driver shares (`driver`,
`label`, `poll_s`, `signals:` overrides) around the driver's own `config:`,
which may also sit flat beside the envelope. The simulated drivers:

| driver | config | signals |
| --- | --- | --- |
| `sim_daq` | `link` (a `sim_plant` or `sim_furnace`), `ports: {signal: port}` | `[RP]`, one per port; a bare `sim_plant`'s one port is `output` and the file says what it measures: `{port: output, quantity: temperature, unit: "°C"}` |
| `sim_drive` | `link`, `ports: {signal: port}` | `[W]`, in the port's unit: watts for a furnace `heaterN` (limits `0..power_w`), `of full` for a bare plant's `input` (limits `0..1`); or spelled out `{port, quantity, unit, limits}` to mirror a real device's unit, the value mapped linearly over `limits` onto the port's drive; or `{port, demand: output, quantity, unit, limits}` to take the demand straight in the plant's own output unit, `commit` inverting the plant to find the drive — `quantity`/`unit` default from the plant if it knows (a furnace zone), `limits` from the plant's static range if its model has one (`Lag`, `Fopdt`; not `Integrator`) |

A dotted key in `ports` (`dry.humidity: dry_h`) puts the signal in a
namespace, one atomic namespace per prefix, so a sim overlay can mirror a
namespaced real device address for address (`hum_sensors.dry.humidity`).
Range, precision, `warn`/`alarm` bands, a longer `poll_s` for one signal,
or narrower `limits` go in the envelope's `signals:`; the plant's own
parameters (and its sensor lag and noise) are on the link, adjustable live
through `flyball sim set`. Several devices share one link, which is how the
furnace's zones interact. `sim_daq` has the simulation-only commands `fail`
and `restore` (a signal at a time); `sim_drive` has `disturb`, its `offset`
in the signal's unit (watts on a furnace heater).

`oven.yaml`, `tank.yaml`, `chiller.yaml` and `dual.yaml` declare their
drive `demand: output`, so the signal takes a demand straight in the
plant's own output unit (°C, L) and `commit` inverts the plant to find the
drive -- no feedforward is needed (the default `setpoint` one hands the
target its setpoint unchanged, since the units agree) and the law's gains
are ordinary per-unit-of-error PI, even on the chiller's negative-gain
plant -- see the comments in each file.

## Editing the files

The `# yaml-language-server: $schema=rig.schema.json` line at the top of
each file points a YAML editor (the VS Code YAML extension) at the rig
file's schema for completion and checking. The schema is generated from the
installed configs, so write it next to the files once:

```bash
uv run flyball rig schema > ../examples/simulated/rig.schema.json
uv run flyball rig check ../examples/simulated/oven.yaml        # the same check, from the shell
```

A program file gets the same from `flyball program schema` and a
`# yaml-language-server: $schema=program.schema.json` first line.

## Config-only

Nothing in these files is Python. `sim_plant` is a link — one plant shared
by the `sim_daq` that reads its output and the `sim_drive` that drives its
input — and a controller names their signals by address. The same file with
`tag: visa` links and `scpi` devices is a real bench: the schema, routes,
telemetry and CLI are identical either way, which is the property the rig
file exists to demonstrate.
