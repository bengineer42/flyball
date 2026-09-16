# Rig file schema

A rig file describes links, devices and controllers, and builds them in
that order. `.yaml` is the documented form (deep envelopes read better,
and programs are YAML already); `.toml` and `.json` are accepted with the
same shape.

```python
from flyball.runtime.config import load_rig, load_rig_config, rig_schema

rig = load_rig("rig.yaml")            # validate and build
config = load_rig_config("rig.yaml")  # validate only
schema = rig_schema()                 # JSON Schema for an editor
```

Unknown keys are refused at every level. A device that names a link must
name one declared under `links`; a controller must name a source and
target address that resolve; a unit symbol must be one the units table
knows. All of these fail at load with the offending name — `flyball rig
check FILE` reports the same way.

Device and controller tables are **keyed by name**, so a duplicate is a
parse error before flyball sees it, which is why the file needs a strict
YAML loader that rejects duplicate keys (plain PyYAML keeps the last
silently). Links are a separate namespace — a link may share a name with a
device.

## Top level

| key | type | |
| --- | --- | --- |
| `name` | string | optional |
| `board` | string | a board profile: a name on the board path (`$FLYBALL_BOARDS`, `boards/` beside or above the file, `~/.config/flyball/boards`, `/etc/flyball/boards`), or a path relative to the file; its `links` are added underneath the file's own, and `pin: "LABEL"` on a device resolves against its `pins` |
| `recording` | bool | open a session when the daemon starts |
| `clock` | `{speed?, stepped?}` | run the rig's time faster (`speed`, default 1×), or only when stepped (`stepped`, for a batch run or a test); refused unless every link is `sim_*`/`fake_*` |
| `extends` | `[path, …]` | this file's own bases, resolved and merged (in order) before this file's own keys are layered on top; the command line's own overlay list still wins |
| `links` | `{name: Link}` | declared once, referred to by name |
| `devices` | `{name: DeviceEntry}` | the envelope + the driver's own config, [flat or layered](#devices) |
| `controllers` | `{target-address: ControllerEntry}` | keyed by the writable signal driven |

## Several files: overlays

A rig is an ordered list of files, later overlaying earlier — the
docker-compose `-f` / kustomize / Hydra pattern:

```
flyball-daemon furnace.yaml sim.yaml
flyball rig check furnace.yaml sim.yaml --set devices.furnace.config.noise=0.3
```

`merge(base, overlay)` (`flyball.runtime.overlay`) is the one rule:
mappings deep-merge key by key, everything else (scalars, lists) replaces
whole, and a key whose overlay value is `null` is removed from the result
— the only way to delete something an earlier layer set (a real link,
before a `sim_*` one takes its place). `--set KEY=VALUE` parses `KEY` as a
dotted path and `VALUE` as a YAML scalar (so `null` deletes there too) and
applies as a one-key overlay on top of every file. `extends:` inside a
file resolves the same way, under that file, before the command line's
files are merged with each other.

The point of an overlay is that it swaps the **drivers** behind the same
device and signal names, so every address, controller, dashboard, program
and recorded session is identical whether the rig is real or simulated.
`examples/simulated/furnace.yaml` demonstrates the pattern in one file (a
`sim_daq`/`sim_drive` pair standing in for a thermocouple DAQ and an SSR
bank that don't exist yet); `examples/humidity/rig.yaml` + `sim.yaml` is
the real two-file form — read both.

## Devices

Every device entry has an **envelope** — flyball's own keys, the same for
every driver — around the driver's own config, which may sit **flat**
beside the envelope or **layered** under `config:`; both parse to the same
thing (checked at import: a driver's config may not declare a field named
like an envelope key).

| envelope key | type | |
| --- | --- | --- |
| `driver` | string | which driver builds this device; a tag on the process-wide `Config.registry` |
| `label` | string, optional | shown instead of the name |
| `poll_s` | number, optional | inherited down the tree; a namespace or signal override wins |
| `signals` | `{name: SignalOverride \| NamespaceOverride}` | per-signal metadata overrides and access restriction — never adds access the driver did not declare |
| `bound` | `{role: address}` | inputs this device follows on another device: a `role` on the driver's `Input` declarations, resolved to the address's `Signal`/`Node` and read as `self.<input>.value` in `commit` |
| `config` | object | the driver's own settings, if not given flat |

```yaml
devices:
  wet_supply: { driver: sht4x, label: Wet supply, poll_s: 5, link: i2c1, address: 0x46 }   # flat

  hum_sensors:                                                                       # layered
    driver: sht4x_set
    label: Humidity sensors
    poll_s: 1
    config: { link: i2c1, sensors: { chamber: { address: 0x44 }, dry: { address: 0x45 }, wet: { address: 0x46 } } }
    signals:
      chamber: { signals: { humidity: { warn: [20, 80] } } }
      dry:     { poll_s: 5 }
```

(from the plan's worked example — `examples/humidity/rig.yaml` is the real
file this became).

A `SignalOverride` is `{label, range, precision, warn, alarm, poll_s,
limits, access, readable, publishing, writable}`: the first
group replaces metadata the driver declared; `access` names the set to
keep (`"r"`), and `readable`/`publishing`/`writable` drop one flag each and
take only `false` — the driver declares what it can honour, the file
cannot add to it. A `NamespaceOverride` is `{label, poll_s, signals}`,
recursing the same way into a namespace's own children.

## Links

Every link is a tagged config, declared once under `links:` and referred
to by name from a device's `link` field (`config.link`, or flat). The
generic drivers this library ships:

| `tag` | is | fields |
| --- | --- | --- |
| `visa` | a `TextLink` | `resource` (`TCPIP::…::INSTR`, `USB0::…::INSTR`, `ASRL/dev/ttyUSB0::INSTR`), `timeout_ms` = 2000, `backend` = `@py` |
| `serial` | a `TextLink` | `port`, `baud` = 9600, `terminator` = `\n`, `timeout_s` = 1 |
| `fake_text` | a `TextLink`, scripted | `replies: {command: reply}` |
| `modbus_tcp` | a `RegisterLink` | `host`, `port` = 502 |
| `modbus_rtu` | a `RegisterLink` | `port`, `baud` = 9600 |
| `fake_registers` | a `RegisterLink`, scripted | `registers: {address: value}` |
| `sim_plant` | a shared plant, one input one output | `model` = `lag` \| `integrator` \| `fopdt`, `tau_s`, `dead_s`, `gain`, `leak`, `ambient`, `initial`, `noise`, `seed` |
| `sim_furnace` | a shared multi-zone plant | `zones`, `power_w`, `capacity_j_per_k` (each a number or one per zone), `coupling_w_per_k`, `loss_w_per_k`, `emissivity`, `area_m2`, `ambient_c`, `sample_capacity_j_per_k`, `sample_coupling_w_per_k`, `sample_zone`, `sensor_lag_s`, `noise`, `seed` |

A driver package registers its own tags the same way: `flyball_linux`
(`/home/ben/flyball/linux`) adds board-facing link tags `i2c`, `spi`,
`gpio`, `pwm`, `onewire` and their `fake_*` counterparts, plus device
drivers such as `sht4x`/`sht4x_set` — see
[Boards and Linux I/O](../3-running/boards.md). `examples/humidity`
additionally registers its own `sim_humidity_chamber` plant link and
`dual_pump_blender` device — see the humidity book.

## The generic devices

| `tag` | is | fields |
| --- | --- | --- |
| `scpi` | one device, its tree in `channels:` | `link`, `channels: {name: {query?, write?, unit, quantity?, scale?}}` — `query` alone is `[RP]`, `write` alone `[W]`, both `[RPW]`; `write` is a command template with `{value}` |
| `modbus` | one device, its tree in `registers:` | `link`, `registers: {name: {address, kind: holding\|input\|coil, unit, scale?, write?}}`, `unit_id` = 1; `write: true` makes a register `[RPW]` (refused on `kind: input`) |
| `sim_daq` | reads a `sim_plant`/`sim_furnace` link's outputs as `[RP]` signals | `link`, `ports: {signal-path: port-name \| {port, quantity, unit, limits?}}` — a dotted path (`dry.humidity`) puts the signal in a namespace; commands `fail(signal)`/`restore(signal)`, simulation-only |
| `sim_drive` | drives a `sim_plant`/`sim_furnace` link's inputs from `[W]` signals | `link`, `ports: {signal-path: port-name \| {port, demand: input\|output, quantity?, unit?, limits?}}` — `demand: input` (default) maps the signal linearly onto the port's 0–1 drive; `demand: output` declares the signal in the plant's own unit and inverts its static model, when it has one |

`bench.yaml` (`examples/simulated/bench.yaml`, quoted in full):

```yaml
links:
  psu: { tag: fake_text, replies: { "MEAS:VOLT?": "11.98", "*IDN?": "Fake Instruments,PSU-1,0,1.0" } }
  dmm: { tag: fake_text, replies: { "MEAS:VOLT:DC?": "+1.1980E+01", "MEAS:CURR:DC?": "0.250" } }

devices:
  psu:
    driver: scpi
    label: Bench PSU
    poll_s: 1.0
    config:
      link: psu
      channels:
        set_voltage:    { write: "SOUR:VOLT {value:.3f}", unit: V }   # [W]
        output_voltage: { query: "MEAS:VOLT?", unit: V }              # [RP]
    signals:
      set_voltage:    { limits: [0, 30] }
      output_voltage: { precision: 3 }
  dmm:
    driver: scpi
    label: Bench DMM
    poll_s: 0.5
    config:
      link: dmm
      channels:
        voltage: { query: "MEAS:VOLT:DC?", unit: V }
        current: { query: "MEAS:CURR:DC?", unit: A }
    signals:
      voltage: { range: [0, 30], precision: 3 }
      current: { range: [0, 3],  precision: 3 }

controllers:
  psu.set_voltage: { signal: dmm.voltage, law: { tag: PI, kp: 0.5, ki: 0.1 }, default: true }
```

Swap the link `tag` for `visa` (and `replies:` for a `resource:`) and this
is the real bench — one `scpi` driver for the whole instrument on each
side, no separate reader/actuator pair.

The `flyball-linux` package adds board-aware drivers (`i2c`, `gpio`,
`sht4x`, PWM devices, …) — see [Boards and Linux I/O](../3-running/boards.md).
Any device entry may say `pin: "LABEL"` (flat, or under `config`) instead
of the link/line fields, when the file has a `board`: the board's fields
for that label fill in, and anything the entry already gives wins.

## Controllers

Keyed by the **target's address** — a controller is named by the writable
signal it drives.

| key | type | |
| --- | --- | --- |
| `signal` | address | the source: a publishing (`P`) signal |
| `law` | `{tag, ...gains}` | e.g. `{tag: PI, kp: 0.2, ki: 0.05}`; omit for none |
| `feedforward` | `{tag, ...}` | maps the source's unit to the target's: `setpoint`, `none`, `affine {gain, bias, rate_gain?}`, `table {points, rate_gain?}`; omit for `setpoint` when the units agree, else `none` |
| `default` | bool | the controller a command means when it names none; at most one per file |
| `min_period_s` | number, optional | step the law at most this often |

```yaml
controllers:
  heaters.heater1: { signal: furnace.zone1, law: { tag: PI, kp: 100, ki: 0.15, tt: 30 } }
  heaters.heater2:
    signal: furnace.zone2
    law: { tag: PI, kp: 100, ki: 0.15, tt: 30 }
    feedforward: { tag: table, rate_gain: 3000, points: [[20, 0], [200, 289.4], [400, 659.8]] }
    default: true
```

(`examples/simulated/furnace.yaml`, abridged). `rate_gain` (`affine`,
`table`) adds `rate_gain * rate` to the demand, `rate` being the
setpoint's own rate of change in the source's unit *per second* (zero off
a ramp): target unit per source-unit-per-second — a zone's
`capacity_j_per_k` (J/K = W per °C/s) is the extra power a ramp needs to
charge its own thermal mass. Not on `setpoint`: that feedforward already
hands the target the source's own unit, so a rate term there would be a
lead compensator, a different job from the plant-capacity model this is.

## Example

See [Supported equipment](../3-running/equipment.md) for more complete
files, and the humidity book for a real two-file (hardware + simulated
overlay) rig.
