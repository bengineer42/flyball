# Building an application

First check whether you need to write anything. A SCPI or Modbus
instrument is a table in a rig file, and a QCoDeS or PyMeasure driver wraps
in one line — see [Supported equipment](../3-running/equipment.md). This
part is for hardware that is none of those.

Putting flyball on your own hardware means writing three things, in this
order:

1. **Measurands and sources** — what is measured. A few lines.
2. **A reader** — the device that produces samples for those sources.
3. **An actuator** — the device a loop drives, with `set_demand`.

Then assemble them into a rig, attach a loop, and serve it. Nothing below the
application — the loop, the runtime, the HTTP API, the CLI — needs to change.
The extension points are:

| to add | write | you get |
| --- | --- | --- |
| a sensor | a `Reader` with `read` or `push`, typed `config`/`settings`/`state` properties, `@command` methods | routes under `/api/readers`, telemetry, CLI subcommands, its sources in the schema |
| an actuator | a `Device` with `set_demand`, the same properties and commands | a loop that drives it; routes under `/api/actuators`, telemetry, CLI subcommands, a client method per command |
| a control law | a class with `step` | a tag usable in files and requests, config/state/view models |
| a trajectory | a class with `generate` | the same |
| a command | a frozen dataclass with `run` | a request model, a spelling in program files |
| a wait | an `Activity` | listed, fired or interrupted from the API |

The running example throughout this part is a simulated oven — a first-order
lag read by a probe and driven by a heater — in
[`oven.py`](../snippets/oven.py). It is complete and runs without hardware:

```
cd book/src/snippets && python oven.py
after 2 min: 100.9 °C, demand 95.5
```

Chapters:

- [Writing a sensor](sensor.md) — measurands, sources, polled and pushed readers
- [Writing an actuator](actuator.md) — `set_demand`, the three tiers, commands, conditions
- [Config and build](config.md) — describing a device so it can be built from a file
- [Assembling a rig](rig.md) — readers, loops, recording, serving, or the same from a file
