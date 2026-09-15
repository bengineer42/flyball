# Architecture

Four layers. Nothing below imports anything above.

```
application     what is controlled, and with what hardware
                a quantity, an actuator, the devices that realise them

runtime         the timebase and I/O: clock, readers, the tick, telemetry,
                recording, signals, and the sequencing of commands into programs

control         a loop, a control law, a reference trajectory, and the
                arithmetic of handing control over. Beside it, identification
                and the tuning rules

core            values and infrastructure with no opinions: time, units,
                readings, devices, errors, resources, publish/subscribe, config
```

Beside the library sit its **surfaces**: the HTTP/websocket server, the
program file dialect, and a client and CLI built from what the server
publishes. Nothing below them knows they exist.

The application layer sits *beside* the runtime, not above it: it implements
protocols the runtime defines rather than being called by name. That is what
makes a second application a matter of writing an actuator rather than
editing the loop.

## Packages

| package | layer | holds |
| --- | --- | --- |
| `flyball.core` | core | `Clock`, `Time`, `Duration`, `Rate`; `units`; `Measurand`, `Source`, `Channel`, `Reading`, `Sample`; `Device`, `Reader`, `Actuator`, `Observer`; `Config`; errors; `Topic`, `Latest`, `Signal` |
| `flyball.control` | control | `Loop`, `ControlLaw` and the laws, `SetPointGenerator`, `Tuning`, `Transfer` |
| `flyball.autotune` | control | `StepTest`, `RelayTest`, `FOPDT`, `Ultimate`, the rules |
| `flyball.adaptive` | control | `Identifier`, `RecursiveLeastSquares`, `SelfTuner` |
| `flyball.runtime` | runtime | `Rig`, `Readers`, `Loops`, `Signals`, `Recorder` |
| `flyball.programmer` | runtime | `Command`, `Activity`, `Program`, `Programmer` |
| `flyball.runtime.config` | runtime | `RigConfig`, `load_rig`, `rig_schema`: a rig as a file |
| `flyball.db` | runtime | `Store`, `SessionWriter`, `SqliteStore`, row types; `documents` for the Bluesky event model |
| `flyball.devices` | application | `ScpiReader`/`ScpiActuator`, `ModbusReader`/`ModbusActuator`: table-driven devices with tagged configs |
| `flyball.sim` | — | a stepped clock, a lag plant, a function reader, a recording actuator; `runtime` never imports it |
| `flyball.hardware` | application | `I2CBus`, `I2CMux`, `Bank`; `links`: `TextLink` and `RegisterLink` with VISA, serial, Modbus and fake implementations |
| `flyball.server` | surface | the FastAPI app, routes, wire models, the program dialect |
| `flyball.client`, `flyball.cli` | surface | pure HTTP; import nothing from the rig |
| `flyball.integrations` | surface | adapters: Bluesky documents, QCoDeS and PyMeasure instruments as devices |

## The pattern

The same thing four times: a registry keyed by tag, populated on
subclassing, with a pydantic model derived from the class itself.

| registry | populated by | model derived from |
| --- | --- | --- |
| control laws | `class X(ControlLaw, tag=…)` | `__init__` → config; `_state_fields` → state |
| trajectories | `class X(SetPointGenerator, tag=…)` | the same |
| commands | `class X(Command, tag=…)` | the dataclass constructor → request |
| configs | `class X(Config, tag=…)` | the model itself; `union` discriminates on `tag` |

Devices do the same without a registry: `config`, `settings` and `state`
types are read off property annotations on subclassing, and `@command`
methods are collected. Everything that faces a person — routes, CLI
subcommands, forms, program steps — is derived from those models, so nothing
is described twice.

## Dependencies

`flyball.core` has none: the pure controller, the device protocol, the
simulated plant and the SQLite store are stdlib-only, so a downstream package
can depend on the algorithm without pulling in a serial stack. Extras:
`web` (FastAPI, uvicorn), `cli` (httpx, websockets), `serial`, `visa`,
`modbus`, `bluesky`, `qcodes`, `pymeasure`. Each driver is imported only
when a real link or wrapper is built.

## Layering enforcement

`import-linter` is a dev dependency and `pyproject.toml` carries a layers
contract, but the contract names packages from an earlier layout
(`flyball.web`, `flyball.devices`, `flyball.store`, `flyball.types`) and
does not check the current one. The layering above is a convention until it
is updated.

## Where it is going

Two intentions shape the extension points:

1. **Equipment and loops as packages.** A `flyball-<device>` distribution
   defines a source, a reader or an actuator, and is usable by name the
   moment it is installed.
2. **Use through config, not code.** A rig is a file: which links, which
   readers, which loop on which channel with which law and actuator.

The second exists for the generic devices (`flyball.runtime.config`). The
first still needs per-rig registries instead of process-wide ones, a frozen
public surface, and entry-point discovery. `IDEAS.md` in the repository
carries the detail.
