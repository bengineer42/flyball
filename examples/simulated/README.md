# Simulated rigs

Rigs with nothing plugged in, written as files. Each one runs the real
runtime, server, recorder and CLI; only the devices are pretend. They are
here to try the system, to test against, and to show what a rig file is.

| file | plant | what it shows |
| --- | --- | --- |
| `oven.toml` | first-order lag with dead time, noisy | the classic control problem; the autotune case |
| `tank.toml` | integrator with a drain | a plant with no natural rest; why integral action matters |
| `bench.toml` | scripted SCPI supply and meter over `fake_text` links | the exact file shape a real bench uses — change two link tags and it is hardware |
| `furnace.toml` | three heated zones in a row, a sample, thermocouples that lag; radiative losses | the complicated one: loops that interact, a gain that changes with temperature, a sensor you can fail, a two-hour firing at 60× |
| `chiller.toml` | a lag with a *negative* gain | reverse-acting: the actuator cools, so the plant rests at ambient and falls as the demand rises |
| `dual.toml` | an oven (°C) and a tank (L), independent | one rig file is not one plant: two loops in different units, neither touching the other |

Rigs built to break things on purpose — many more channels, absurd sample
rates, every unit the registry knows, deliberately bad tuning — live in
[`../stress/`](../stress/README.md).

## The furnace

`furnace.toml` is a `sim_furnace` link read through four `sim_reader`s and
driven by three `sim_actuator`s, each naming a `port` of the one plant. The
heaters are plain: a demand is a power in watts, and each loop carries a
`table` feedforward -- the single-zone losses curve, as one would measure it
at commissioning -- so the law only has to cover what the neighbours add. The
zones conduct heat to each other, so holding the middle at 900 °C drags the
ends above their 600 °C setpoints with their heaters off — a heater cannot
cool — and losses are convective plus radiative, so the drive a zone needs
for the same step is several times larger at 900 than at 200.
Five programs in `programs/`, each a different lesson:

| program | furnace time | shows |
| --- | --- | --- |
| `firing.yaml` | 2.5 h | all three to 600, the middle to 900 — the ends get dragged above their setpoints with heaters off |
| `gradient.yaml` | 4 h | 800 / 600 / 400 along the tube — conduction makes the hot end saturate and the cold end's heater idle |
| `anneal.yaml` | 8 h | a driven 2 °C/min cooldown — the drive falls smoothly until the programmed rate exceeds the natural one |
| `step-test.yaml` | 5 h | identification steps on zone 2 at 300 and at 700 — the same 30 °C step, a different response: the case for a gain schedule |
| `load-sample.yaml` | a few minutes | the operator in the loop: two `wait` steps with a timeout, for trying the go button |

```bash
uv run flyball-daemon ../examples/simulated/furnace.toml      # clock at 60x from the file
uv run flyball program run ../examples/simulated/programs/firing.yaml
uv run flyball program status
uv run flyball sim                                              # the tube's parameters
uv run flyball sim set tube coupling_w_per_k=20                 # couple the zones harder, live
uv run flyball zone3 fail                                       # open-circuit a thermocouple
uv run flyball watch loops
```

`flyball program run ../examples/simulated/programs/firing.yaml` starts a
firing; `flyball program status` says where it is; `flyball signal fire wait`
answers the operator prompt at the end. With `clock = { stepped = true }` instead of a speed, the same firing runs to
completion in the time the arithmetic takes — every poll, tick and hold in
order — which is how `tests/test_furnace.py` tests it.

## Run one

```bash
cd controller
uv run python ../examples/simulated/demo.py ../examples/simulated/oven.toml 50 600
```

steps the rig's clock through ten minutes and prints the loop settling.
For the daemon and everything on top of it:

```bash
uv run flyball-daemon ../examples/simulated/oven.toml --record
flyball status                   # readers, loops, actuators, signals at a glance
flyball sim clock 60             # a simulated minute per second; `flyball sim` for the knobs
flyball actuators                # heater: SimActuator: set_limits, disturb
flyball heater disturb --offset -0.3        # open the door
flyball watch loops
```

## Editing the files

The `#:schema rig.schema.json` line at the top of each file points a TOML
editor (Taplo, the VS Code "Even Better TOML" extension) at the rig file's
schema for completion and checking. The schema is generated from the
installed configs, so write it next to the files once:

```bash
uv run flyball rig schema > ../examples/simulated/rig.schema.json
uv run flyball rig check ../examples/simulated/oven.toml        # the same check, from the shell
```

A program file gets the same from `flyball program schema` and a
`# yaml-language-server: $schema=program.schema.json` first line.

## Config-only

Nothing in these files is Python. `sim_plant` is a link — one plant shared
by the reader that reads its output and the actuator that drives its input
— and the loop names them by name. The same file with `tag = "visa"` links
and `scpi_reader` devices is a real bench: the schema, routes, telemetry and
CLI are identical either way, which is the property the rig file exists to
demonstrate.
