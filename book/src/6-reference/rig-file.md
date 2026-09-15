# Rig file schema

A rig file describes links, readers, actuators and loops, and builds them
in that order. `.toml`, `.yaml` or `.json`; TOML reads best for config.

```python
from flyball.runtime.config import load_rig, load_rig_config, rig_schema

rig = load_rig("rig.toml")            # validate and build
config = load_rig_config("rig.toml")  # validate only
schema = rig_schema()                 # JSON Schema for an editor
```

Unknown keys are refused at every level. A device that names a link must
name one declared under `links`; a loop must name a channel a reader
declares and an actuator listed above; a unit symbol must be one the units
table knows. All of these fail at load with the offending name.

## Top level

| key | type | |
| --- | --- | --- |
| `name` | string | optional |
| `board` | string | a board profile: a name on the board path, or a path relative to the file; its links go under `links`, its pins resolve `pin` on devices ([boards](../3-running/boards.md)) |
| `recording` | bool | open a session when the daemon starts |
| `clock` | `{speed?, stepped?}` | run the rig's time faster, or only when stepped; simulated rigs only |
| `links` | `{name: Link}` | declared once, referred to by name |
| `readers` | `[{device: Reader, period_s?}]` | `period_s` > 0 polls; omit for a pushed reader |
| `actuators` | `[Actuator]` | |
| `loops` | `[{channel, actuator, law?, default?}]` | `channel` is `source.measurand` |

## Links

Every link is a tagged config. A device's `link` field takes either a name
from `links` or an inline link.

| `tag` | fields | extra | fake |
| --- | --- | --- | --- |
| `visa` | `resource` (`TCPIP::…::INSTR`, `USB0::…::INSTR`, `ASRL/dev/ttyUSB0::INSTR`), `timeout_ms` = 2000, `backend` = `@py` | `visa` | `fake_text` |
| `serial` | `port`, `baud` = 9600, `terminator` = `\n`, `timeout_s` = 1 | `serial` | `fake_text` |
| `modbus_tcp` | `host`, `port` = 502 | `modbus` | `fake_registers` |
| `modbus_rtu` | `port`, `baud` = 9600 | `modbus` | `fake_registers` |
| `fake_text` | `replies: {command: reply}` | — | |
| `fake_registers` | `registers: {address: value}` | — | |
| `sim_plant` | `kind` = lag / integrator / fopdt, `tau_s`, `dead_s`, `gain`, `leak`, `ambient`, `initial`, `noise`, `seed` | — | is one |
| `sim_furnace` | `zones`, `power_w`, `capacity_j_per_k` (each a number or one per zone), `coupling_w_per_k`, `loss_w_per_k`, `emissivity`, `area_m2`, `ambient_c`, `sample_*`, `sensor_lag_s`, `noise` | — | is one |

## Devices

| `tag` | is | fields |
| --- | --- | --- |
| `scpi_reader` | reader, one source | `name`, `link`, `measurands: {name: {query, unit, label?, range?, precision?}}`, `source?` (defaults to `name`) |
| `scpi_actuator` | actuator | `name`, `link`, `command` (with `{value}`), `demand_unit?`, `readback?` |
| `modbus_reader` | reader, one source | `name`, `link`, `registers: {name: Register}`, `unit_id` = 1, `source?` |
| `modbus_actuator` | actuator | `name`, `link`, `output: Register`, `unit_id` = 1 |
| `sim_reader` | reader, one source | `name`, `link`, `port?` (a multi-port plant's output), `measurand`, `unit`, `range?`, `precision?`; commands `fail`, `restore` |
| `sim_actuator` | actuator | `name`, `link`, `port?` (a multi-port plant's input), `limits`, `unit?`; commands `set_limits`, `disturb` |

The tags `flyball-linux` adds (`i2c`, `gpio`, `sht4x`, `pwm_actuator`, ...)
are on the [boards page](../3-running/boards.md). Any device entry may say
`pin = "LABEL"` instead of the link and line, when the file has a `board`.

A `Register` is `{address, kind, scale, offset, word_order, unit, label,
range, precision}`; only `address` is required. `kind` is `u16` (default),
`s16`, `u32`, `s32` or `f32`; `word_order` is `big` (default) or `little`;
`value = raw * scale + offset`.

## Loops

| key | type | |
| --- | --- | --- |
| `channel` | `source.measurand` | the controlled variable |
| `actuator` | name | must be listed under `actuators` |
| `law` | `{tag, ...gains}` | e.g. `{tag = "PI", kp = 0.2, ki = 0.05}`; omit for none |
| `default` | bool | the loop a command means when it names none |

## Example

See [Supported equipment](../3-running/equipment.md#a-rig-file) for a
complete file, and the humidity book for a real one.
