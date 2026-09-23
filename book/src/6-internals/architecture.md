# Architecture

This part is for changing flyball itself. Using it is the earlier parts:
[Running a rig](../1-running/runner/index.md), [Configuration](../2-config/index.md),
[Extending](../3-extending/index.md), [The server](../4-server/index.md).
Built as the layers `import-linter`'s contract enforces (see Layering,
below), top to bottom -- each may import anything below it, nothing below
imports anything above:

```
runner          the flyball-runner entry point: cli, starting, serving

interfaces      the FastAPI/websocket server, the MCP server, the program
                dialect, and a pure-HTTP client/CLI built from what the
                server publishes

sequencing      commands sequenced into programs, over a running rig

runtime         the timebase and I/O: clock, polling, delivery, telemetry,
                recording, retention -- plus `rig`, the runtime container
                (devices, links, controllers, triggers, polling), which is
                genuinely coupled to it both ways and so is not yet a layer
                of its own

hardware        link protocols, beside identification (`adaptive`), the
                tuning rules (`autotune`) and the sqlite recorder (`record`)

library         saved, named configs -- tunings so far

control         the 9 built-in control laws, feedforwards and setpoint
                generators -- plus `model` (`Catalog`/`Config`/`Instance`
                type-registration), the base every one of them, and a
                device driver's own config, derives from, which is
                likewise coupled both ways and not yet a layer of its own

foundation      values and infrastructure with no opinions: time, units,
                quantities, signals, devices, errors, resources,
                publish/subscribe
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
| `flyball.foundation` | foundation | `Clock`, `Time`, `Duration`, `Rate`; `units`; `Quantity`; `Signal`, `Node`, `Path`, `Reading`, `Sample`, `Write`, `WriteState`, `Access`; `Device`, `DriverConfig`; errors; `Topic`, `Latest`, `Trigger` |
| `flyball.model` | control (unlisted -- a real cycle, see Layering) | `Catalog`/`Catalogs`, `Config`; the base `ControlLaw`, `Feedforward`, `SetPointGenerator`, `Controller`/`ControllerSettings`/`ValueSource`, `Transfer` every registered law, feedforward, generator and `DriverConfig` derives from |
| `flyball.control` | control | the 9 built-in laws (`P`, `PI`, `PID`, `IMC`, `OnOff`, `OpenLoop`, `Scheduled`, `SlidingMode`, `SmithPredictor`), the `Affine`/`Table` feedforwards, the `Dwell`/`LinearRampSetpoint`/`Profile` generators |
| `flyball.library` | library | `Tuning`, `Tunings` -- saved, named configs |
| `flyball.autotune` | `hardware\|adaptive\|autotune\|record` | `StepTest`, `RelayTest`, `FOPDT`, `Ultimate`, the rules |
| `flyball.adaptive` | `hardware\|adaptive\|autotune\|record` | `Identifier`, `RecursiveLeastSquares`, `SelfTuner` |
| `flyball.hardware` | `hardware\|adaptive\|autotune\|record` | `I2cLink`, `Bank`; `links`: the `TextLink`/`RegisterLink` protocols only -- their fakes, real implementations (VISA, serial, Modbus) and the table-driven `scpi`/`modbus` devices built over them live in `extensions/visa`, `extensions/modbus` |
| `flyball.record` | `hardware\|adaptive\|autotune\|record` | `Store`, `SessionWriter`, `SqliteStore`, row types; `documents` for the Bluesky event model |
| `flyball.rig` | runtime (unlisted -- a real cycle, see Layering) | `Rig`, `Controllers`, `Polling`, `Triggers` -- the runtime container: devices, links, controllers, triggers, polling |
| `flyball.runtime` | runtime | `Recorder`, `Writer`, retention, stats, `drivers`; `runtime.config`: `RigConfig`, `load_rig`, `rig_schema` — a rig as a file, with overlays |
| `flyball.sequencing` | `sequencing` | `Step`, `Activity`, `Program`, `Programmer` |
| `flyball.interfaces.server` | `mcp\|server` | the FastAPI app, routes, wire models, the program dialect |
| `flyball.interfaces.mcp` | `mcp\|server` | the MCP server (stdio and mounted), tools, guides; built entirely on `flyball.interfaces.client` |
| `flyball.interfaces.client`, `flyball.runner`, `flyball.scaffold` | `runner` (client and scaffold stand outside the contract, see below) | pure HTTP; import nothing from the rig. The `flyball` CLI itself is a separate Go binary (`daemon/cmd/flyball`), not part of this package |

## The pattern

The same thing four times: a registry keyed by type, populated on
subclassing, with a pydantic model derived from the class itself.

| registry | populated by | model derived from |
| --- | --- | --- |
| control laws | `class X(ControlLaw, type=…)` | `__init__` → config; `_state_fields` → state |
| trajectories | `class X(SetPointGenerator, type=…)` | the same |
| program steps | `class X(Step, tag=…)` | the dataclass constructor → request |
| configs | `class X(Config, type=…)` | the model itself; `union` discriminates on `type` |

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
flyball.sequencing
flyball.runtime
flyball.hardware | flyball.adaptive | flyball.autotune | flyball.record
flyball.library
flyball.control
flyball.foundation
```

`flyball.rig` and `flyball.model` are deliberately left out of this list,
same as `flyball.scaffold`: each has a real, unavoidable cycle rather than
an oversight. `flyball.rig` and `flyball.runtime` import each other at real
module level (`runtime.config`/`writer`/`retention` build or type a `Rig`;
`rig.rig` imports `runtime.writer`), so neither can sit below the other yet
-- the fix is moving `writer.py`/`retention.py` into `rig/` and extracting
just the `Rig`-building half of `runtime.config` out of it. `flyball.model`
has to sit below `flyball.control` (the built-in laws import `ControlLaw`/
`Feedforward`/`SetPointGenerator` from it) but also below `flyball.foundation`,
model's own supposed base layer (`foundation.device.device.DriverConfig`
subclasses `model.config.Config`) -- the fix is moving `DriverConfig` itself
into `model/`. Both checked directly with `uv run lint-imports`; the
reasoning is in `pyproject.toml`'s own comment above the contract.

A second contract keeps `flyball.interfaces.client`, `flyball.scaffold` and
`flyball.interfaces.mcp` standing apart from all of it: none may import
`flyball.foundation`, `flyball.control`, `flyball.model`, `flyball.library`,
`flyball.runtime`, `flyball.rig`, `flyball.interfaces.server` or
`flyball.sequencing`, so a client built from the wire alone -- and the MCP
server built on that client -- cannot quietly start depending on the rig's
internals.

## Where it is going

Two intentions shape the extension points:

1. **Devices and control laws as packages.** A `flyball-<device>`
   distribution defines a driver — a device, a control law, a feedforward —
   and is usable by name (its type) the moment it is installed.
2. **Use through config, not code.** A rig is a file: which links, which
   devices, which controller on which signal with which law.

The second exists for the generic devices (`flyball.runtime.config`). The
first still needs per-rig registries instead of process-wide ones — driver
tags are one process-wide namespace, a `Catalog` per kind held in one
process-scoped `Catalogs` (`flyball.model.catalog`), so a second plugin
declaring the same type still collides; see [Decisions](decisions.md) — a
frozen public surface, and entry-point discovery. `IDEAS.md` in the
repository carries the detail.
