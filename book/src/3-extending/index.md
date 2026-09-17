# Building an application

!!! abstract "Where you are: Extending"
    For the **developer** putting flyball on hardware it has no driver for, or adding a control law: Python against a small device model.

    | if instead you want to… | go to |
    | --- | --- |
    | understand the words first | [Overview](../0-overview/index.md) |
    | operate a rig that is already set up | [Running a rig](../1-running/index.md) |
    | describe a rig: devices, links, controllers, how it is served | [Configuration](../2-config/index.md) |
    | talk to a rig from your own code or a model | [The server](../4-server/index.md) |
    | change flyball itself | [Internals](../6-internals/index.md) |
    | look a key or a route up | [Reference](../7-reference/index.md) |
First check whether you need to write anything. A SCPI or Modbus
instrument is a table in a rig file, and a QCoDeS or PyMeasure driver wraps
in one line — see [Integrations](../5-integrations/index.md). This
part is for hardware that is none of those.

Putting flyball on your own hardware means writing one thing, in three
possible shapes:

1. **A device** — a `Device` subclass declaring a tree of **signals**, each
   readable, publishing or writable. A sensor is a device with only `R`/`P`
   signals; a relay or a PSU only `W`; many real instruments are both.
2. **A config** — a `DriverConfig` that builds it, so a rig file can name it.
3. Optionally, **commands** — methods marked `@command` for anything that
   is not a signal: `stop`, `home`, `fail`.

Then assemble one or more devices into a rig, attach a controller, and
serve it. Nothing below the application — the rig, the runtime, the HTTP
API, the CLI — needs to change. The extension points are:

| to add | write | you get |
| --- | --- | --- |
| a device | a `Readable` and/or `Committable` subclass, its tree as descriptors (`Demand`, `Output`, `Setting`, `ConfigSignal`) in the class body or built from config, `read` and/or `write_signal`/`commit`, `@command` methods | routes under `/api/devices`, telemetry, CLI subcommands, its signals in the schema |
| a control law | a class with `step` | a tag usable in files and requests, config/state/view models |
| a trajectory | a class with `generate` | the same |
| a program command | a frozen dataclass with `run` | a request model, a spelling in program files |
| a wait | an `Activity` | listed, fired or interrupted from the API |

The running example throughout this part is a simulated oven — a
first-order lag read by a probe and driven by a heater — in
[`oven.py`](../snippets/oven.py). It is complete and runs without hardware:

```
$ python oven.py
after 2 min: 100.9 °C, demand 95.4
```

Chapters:

- [Writing a sensor](device/sensor.md) — a `Readable` device: quantities, the tree, polled and pushed reads
- [Writing an actuator](device/actuator.md) — a `Committable` device: `write_signal`/`commit`, roles, commands, conditions
- [Config and build](device/config.md) — describing a device so it can be built from a file
- [Assembling a rig](rig.md) — devices, controllers, recording, serving, or the same from a file
