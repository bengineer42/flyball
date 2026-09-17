# Architecture

This part is for changing flyball itself. Using it is the earlier parts:
[Running a rig](../1-running/daemon/index.md), [Configuration](../2-config/index.md),
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

core            values and infrastructure with no opinions: time, units,
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
| `flyball.core` | core | `Clock`, `Time`, `Duration`, `Rate`; `units`; `Quantity`; `Signal`, `Node`, `Path`, `Reading`, `Sample`, `Demand`, `WriteState`, `Access`; `Device`, `DriverConfig`; `Config`; errors; `Topic`, `Latest`, `Trigger` |
| `flyball.control` | control | `Controller`, `ControlLaw` and the laws (`P`, `PI`, `PID`, `OpenLoop`), `SetPointGenerator`, `Feedforward`, `Tuning`, `Transfer` |
| `flyball.autotune` | `hardware\|adaptive\|autotune\|db` | `StepTest`, `RelayTest`, `FOPDT`, `Ultimate`, the rules |
| `flyball.adaptive` | `hardware\|adaptive\|autotune\|db` | `Identifier`, `RecursiveLeastSquares`, `SelfTuner` |
| `flyball.hardware` | `hardware\|adaptive\|autotune\|db` | `I2CBus`, `I2CMux`, `Bank`; `links`: `TextLink` and `RegisterLink` with VISA, serial, Modbus and fake implementations |
| `flyball.db` | `hardware\|adaptive\|autotune\|db` | `Store`, `SessionWriter`, `SqliteStore`, row types; `documents` for the Bluesky event model |
| `flyball.sim` | `sim\|devices\|integrations.qcodes\|integrations.pymeasure` | a stepped clock, simulated plants (`Lag`, `Fopdt`, `Integrator`, `Furnace`), the generic `sim_daq`/`sim_drive` devices; `runtime` never imports it |
| `flyball.devices` | `sim\|devices\|integrations.qcodes\|integrations.pymeasure` | `Scpi`, `Modbus`: table-driven devices whose tree is declared in their own tagged config |
| `flyball.integrations.qcodes`, `.pymeasure` | `sim\|devices\|integrations.qcodes\|integrations.pymeasure` | instrument libraries wrapped as devices |
| `flyball.runtime` | runtime | `Rig`, `Controllers`, `Polling`, `Recorder`, `Triggers`; `runtime.config`: `RigConfig`, `load_rig`, `rig_schema` — a rig as a file, with overlays |
| `flyball.programmer` | `programmer\|integrations.bluesky` | `Command`, `Activity`, `Program`, `Programmer` |
| `flyball.integrations.bluesky` | `programmer\|integrations.bluesky` | Bluesky documents built from a recorded session |
| `flyball.server` | server | the FastAPI app, routes, wire models, the program dialect |
| `flyball.client`, `flyball.cli`, `flyball.daemon`, `flyball.scaffold` | `cli\|daemon` (client and scaffold stand outside the contract, see below) | pure HTTP; import nothing from the rig |

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

`flyball.core` has none: the pure controller, the device protocol, the
simulated plant and the SQLite store are stdlib-only, so a downstream package
can depend on the algorithm without pulling in a serial stack. Extras:
`web` (FastAPI, uvicorn, PyYAML), `cli` (httpx, websockets, PyYAML), `serial`,
`visa`, `modbus`, `bluesky`, `qcodes`, `pymeasure`. Each driver is imported
only when a real link or wrapper is built.

## Layering

`import-linter`'s layers contract (`pyproject.toml`, checked by `make
imports`) is enforced, and names the packages as they are, top to bottom —
each line may import anything below it, nothing below imports anything
above:

```
flyball.cli | flyball.daemon
flyball.server
flyball.programmer | flyball.integrations.bluesky
flyball.runtime
flyball.sim | flyball.devices | flyball.integrations.qcodes | flyball.integrations.pymeasure
flyball.hardware | flyball.adaptive | flyball.autotune | flyball.db
flyball.control
flyball.core
```

A second contract keeps `flyball.client` and `flyball.scaffold` standing
apart from all of it: neither may import `flyball.core`, `flyball.control`,
`flyball.runtime`, `flyball.server` or `flyball.programmer`, so a client
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
