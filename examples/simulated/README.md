# Simulated rigs

Rigs with nothing plugged in, written as files. Each one runs the real
runtime, server, recorder and CLI; only the devices are pretend. They are
here to try the system, to test against, and to show what a rig file is.

| file | plant | what it shows |
| --- | --- | --- |
| `oven.toml` | first-order lag with dead time, noisy | the classic control problem; the autotune case |
| `tank.toml` | integrator with a drain | a plant with no natural rest; why integral action matters |
| `bench.toml` | scripted SCPI supply and meter over `fake_text` links | the exact file shape a real bench uses — change two link tags and it is hardware |

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
