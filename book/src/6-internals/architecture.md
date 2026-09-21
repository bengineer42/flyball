# Architecture

This part is for changing flyball itself. Using it is the earlier parts:
[Running a rig](../1-running/runner/index.md), [Configuration](../2-config/index.md),
[Extending](../3-extending/index.md), [The server](../4-server/index.md).
Four conceptual layers. Nothing below imports anything above.

```
application     what is controlled, and with what hardware:
                the devices that realise a rig's signals

runtime         the timebase and I/O: clock, polling, the delivery, telemetry,
                recording, controllers wired to signals, and the sequencing
                of commands into programs

control         a controller, a control law, a reference trajectory, and the
                arithmetic of handing control over. Beside it, identification
                and the tuning rules

foundation      values and infrastructure with no opinions: time, units,
                quantities, signals, devices, errors, resources,
                publish/subscribe, config
```

Beside the library sit its **surfaces**: the HTTP/websocket server, the
program file dialect, and a client and CLI built from what the server
publishes. Nothing below them knows they exist.

The application layer sits *beside* the runtime, not above it: it implements
protocols the runtime defines (`Readable.read`, `Committable.apply`/`commit`) rather than
being called by name. That is what makes a second application a matter of
writing a device driver rather than editing the rig.

## Packages

| package | layer | holds |
| --- | --- | --- |
| `flyball.foundation` | foundation | `Clock`, `Time`, `Duration`, `Rate`; `units`; `Quantity`; `Signal`, `Node`, `Path`, `Reading`, `Sample`, `Demand`, `WriteState`, `Access`; `Device`, `DriverConfig`; `Config`; errors; `Topic`, `Latest`, `Trigger` |
| `flyball.control` | control | `Controller`, `ControlLaw` and the laws (`P`, `PI`, `PID`, `OpenLoop`), `SetPointGenerator`, `Feedforward`, `Tuning`, `Transfer` |
| `flyball.autotune` | `hardware\|adaptive\|autotune\|db` | `StepTest`, `RelayTest`, `FOPDT`, `Ultimate`, the rules |
| `flyball.adaptive` | `hardware\|adaptive\|autotune\|db` | `Identifier`, `RecursiveLeastSquares`, `SelfTuner` |
| `flyball.hardware` | `hardware\|adaptive\|autotune\|db` | `I2cLink`, `Bank`; `links`: the `TextLink`/`RegisterLink` protocols only -- their fakes, real implementations (VISA, serial, Modbus) and the table-driven `scpi`/`modbus` devices built over them live in `extensions/visa`, `extensions/modbus` |
| `flyball.record` | `hardware\|adaptive\|autotune\|record` | `Store`, `SessionWriter`, `SqliteStore`, row types; `documents` for the Bluesky event model |
| `flyball.runtime` | runtime | `Rig`, `Controllers`, `Polling`, `Recorder`, `Triggers`; `runtime.config`: `RigConfig`, `load_rig`, `rig_schema` — a rig as a file, with overlays |
| `flyball.programmer` | `programmer` | `Command`, `Activity`, `Program`, `Programmer` |
| `flyball.interfaces.server` | `mcp\|server` | the FastAPI app, routes, wire models, the program dialect |
| `flyball.interfaces.mcp` | `mcp\|server` | the MCP server (stdio and mounted), tools, guides; built entirely on `flyball.interfaces.client` |
| `flyball.interfaces.client`, `flyball.runner`, `flyball.scaffold` | `runner` (client and scaffold stand outside the contract, see below) | pure HTTP; import nothing from the rig. The `flyball` CLI itself is a separate Go binary (`daemon/cmd/flyball`), not part of this package |

## The pattern

The same thing four times: a registry keyed by tag, populated on
subclassing, with a pydantic model derived from the class itself.

| registry | populated by | model derived from |
| --- | --- | --- |
| control laws | `class X(ControlLaw, tag=…)` | `__init__` → config; `_state_fields` → state |
| trajectories | `class X(SetPointGenerator, tag=…)` | the same |
| commands | `class X(Command, tag=…)` | the dataclass constructor → request |
| configs | `class X(Config, tag=…)` | the model itself; `union` discriminates on `tag` |

Devices do the same without a registry: descriptors in the class body (or
built from config) collect into the tree on subclassing, `config`'s return
annotation gives `config_type`, and `@command` methods are collected.
Everything that faces a person — routes, CLI subcommands, forms, program
steps — is derived from those, so nothing is described twice.

## Dependencies

`flyball.foundation` has none: the pure controller, the device protocol and the
SQLite store are stdlib-only, so a downstream package can depend on the
algorithm without pulling in a serial stack. The simulated plant moved out
to `flyball-sim` (`flyball_sim`, `../sim`), its own top-level package with
zero third-party dependencies of its own; `flyball.runtime` never imports
it directly, only through the `flyball.configs` entry point every other
optional package uses. Extras: `web` (FastAPI, uvicorn, PyYAML, and
`flyball-sim`), `cli` (httpx, websockets, PyYAML). Everything that talks to
real hardware, an instrument protocol or another library -- serial, VISA,
Modbus, Bluesky, QCoDeS, PyMeasure -- is its own package under
`extensions/`, each with its own extra of the same name; a driver's real
implementation is imported only when a real link or wrapper is built.

## Layering

`import-linter`'s layers contract (`pyproject.toml`, checked by `make
imports`) is enforced, and names the packages as they are, top to bottom —
each line may import anything below it, nothing below imports anything
above:

```
flyball.runner
flyball.interfaces.mcp | flyball.interfaces.server
flyball.programmer
flyball.runtime
flyball.hardware | flyball.adaptive | flyball.autotune | flyball.record
flyball.control
flyball.foundation
```

A second contract keeps `flyball.interfaces.client` and `flyball.scaffold` standing
apart from all of it: neither may import `flyball.foundation`, `flyball.control`,
`flyball.runtime`, `flyball.interfaces.server` or `flyball.programmer`, so a client
built from the wire alone cannot quietly start depending on the rig's
internals.

## Where it is going

Two intentions shape the extension points:

1. **Devices and control laws as packages.** A `flyball-<device>`
   distribution defines a driver — a device, a control law, a feedforward —
   and is usable by name (its tag) the moment it is installed.
2. **Use through config, not code.** A rig is a file: which links, which
   devices, which controller on which signal with which law.

The second exists for the generic devices (`flyball.runtime.config`). The
first still needs per-rig registries instead of process-wide ones — driver
tags are one process-wide namespace (`Config.registry`) today, so a second
plugin declaring the same tag collides; see [Decisions](decisions.md) — a
frozen public surface, and entry-point discovery. `IDEAS.md` in the
repository carries the detail.
