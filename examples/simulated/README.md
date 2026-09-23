# Simulated rigs

Rigs with nothing plugged in, written as files. Each one runs the real
runtime, server, recorder and CLI; only the devices are pretend. They are
here to try the system, to test against, and to show what a rig file is.

| file | plant | what it shows |
| --- | --- | --- |
| `oven.yaml` | first-order lag with dead time, noisy | the classic control problem; the autotune case |
| `tank.yaml` | a leaky integrator, i.e. a first-order lag | the same lag as the oven, in litres, resting at gain / leak |
| `bench.yaml` | scripted SCPI supply and meter over `fake_text` links | the exact file shape a real bench uses — change two link tags and it is hardware |
| `chiller.yaml` | a lag with a *negative* gain | reverse-acting: the drive cools, so the plant rests at ambient and falls as the drive rises |
| `dual.yaml` | an oven (°C) and a tank (L), independent | one rig file is not one plant: two controllers in different units, neither touching the other |

The complicated one -- three heated zones in a row, controllers that
interact, a gain that changes with temperature, a sensor you can fail, a
two-hour firing at 60x -- moved out to its own package, since it needs
`sim_furnace`: see [`../furnace/`](../furnace/README.md).

Rigs built to break things on purpose — many more channels, absurd sample
rates, every unit the registry knows, deliberately bad tuning — live in
[`../stress/`](../stress/README.md).

## Run one

```bash
cd engine
uv run python ../examples/simulated/demo.py ../examples/simulated/oven.yaml 50 600
```

steps the rig's clock through ten minutes and prints the controller settling.
For the runner and everything on top of it:

```bash
uv run flyball-runner ../examples/simulated/oven.yaml --record
flyball status                   # devices, controllers, activities at a glance
flyball sim clock 60             # a simulated minute per second; `flyball sim` for the knobs
flyball invoke heater disturb signal=drive offset=-0.3    # open the door
flyball watch controllers
```

## The file shape

A rig file is `links:` (transports, or here simulated plants), `devices:`
keyed by name, and `controllers:` keyed by the address of the signal each
one drives. A device entry is the envelope every driver shares (`driver`,
`label`, `poll_s`, `signals:` metadata) with the driver's own fields flat
beside it. The simulated drivers:

| driver | config | signals |
| --- | --- | --- |
| `sim_daq` | `link` (a `sim_plant`, or another package's own `MultiPlant` link such as `../furnace/`'s `sim_furnace`), `ports: {signal: port}` | `[RP]`, one per port; a bare `sim_plant`'s one port is `output` and the file says what it measures: `{port: output, quantity: temperature, unit: "°C"}` |
| `sim_drive` | `link`, `ports: {signal: port}` | a demand (`[RPW]`, its readback the committed value), in the port's unit: `of full` for a bare plant's `input` (limits `0..1`), or watts for `../furnace/`'s `heaterN` (limits `0..power_w`); or spelled out `{port, quantity, unit, limits}` to mirror a real device's unit, the value mapped linearly over `limits` onto the port's drive; or `{port, demand: output, quantity, unit, limits}` to take the demand straight in the plant's own output unit, `commit` inverting the plant to find the drive — `quantity`/`unit` default from the plant if it knows one (a furnace zone), `limits` from the plant's static range if its model has one (`Lag`, `Fopdt`; not `Integrator`) |

A dotted key in `ports` (`dry.humidity: dry_h`) puts the signal in a
namespace, one atomic namespace per prefix, so a sim overlay can mirror a
namespaced real device address for address (`hum_sensors.dry.humidity`).
Range, precision, `warning`/`alarm` bands, a longer `poll_s` for one signal,
or narrower `limits` go in the envelope's `signals:`; the plant's own
parameters (and its sensor lag and noise) are on the link, adjustable live
through `flyball sim set`. Several devices may share one link, which is how
`../furnace/`'s zones interact. `sim_daq` has the simulation-only commands
`fail` and `restore` (a signal at a time); `sim_drive` has `disturb`, its
`offset` in the signal's unit.

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
flyball rig schema > ../examples/simulated/rig.schema.json
flyball rig check ../examples/simulated/oven.yaml        # the same check, from the shell
```

A program file gets the same from `flyball program schema` and a
`# yaml-language-server: $schema=program.schema.json` first line.

## Config-only

Nothing in these files is Python. `sim_plant` is a link — one plant shared
by the `sim_daq` that reads its output and the `sim_drive` that drives its
input — and a controller names their signals by address. The same file with
`type: visa` links and `scpi` devices is a real bench: the schema, routes,
telemetry and CLI are identical either way, which is the property the rig
file exists to demonstrate.
