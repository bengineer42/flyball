# Links

A link is something devices are built *on*: a bus, an instrument connection,
or a simulated plant. Each is a tagged config under `links:`, declared once
and named from a device's `link` field. A link is shared: several devices
may sit on one I²C bus, and a `sim_daq` and a `sim_drive` read and drive
one plant.

```yaml
links:
  psu:  { tag: visa, resource: "TCPIP::10.0.0.5::INSTR" }
  i2c1: { tag: i2c, bus: 1 }
  tube: { tag: sim_furnace, zones: 2, power_w: [1500, 1500] }
```

Every real link has a `fake_*` twin that answers from a script, so the same
devices run with nothing plugged in; see [Simulation](simulation.md#the-overlay-pattern).
`GET /api/drivers` lists every tag the running runner can build.

## Text instruments

A **text link** carries lines: `write(command)` and `query(command) -> reply`.
The [`scpi`](devices/drivers.md#scpi) driver and anything line-oriented sit on one.

### `visa`

Any VISA resource through pyvisa (`pip install flyball[visa]`).

| field | default | |
| --- | --- | --- |
| `resource` | required | `TCPIP::192.168.1.20::INSTR`, `USB0::…::INSTR`, `ASRL/dev/ttyUSB0::INSTR` |
| `timeout_ms` | `2000` | |
| `backend` | `@py` | pyvisa-py; `""` for NI-VISA |

### `serial`

A serial port, one command per line.

| field | default | |
| --- | --- | --- |
| `port` | required | `/dev/ttyUSB0` |
| `baud` | `9600` | |
| `terminator` | `"\n"` | appended to every command, stripped from every reply |
| `timeout_s` | `1.0` | |

### `fake_text`

A scripted instrument: `replies: {command: reply}`. An unknown command
raises, so a typo in a `scpi` table fails on the fake before the bench.

## Register instruments

A **register link** reads and writes numbered registers. The
[`modbus`](devices/drivers.md#modbus) driver sits on one.

### `modbus_tcp`

| field | default | |
| --- | --- | --- |
| `host` | required | |
| `port` | `502` | |

### `modbus_rtu`

| field | default | |
| --- | --- | --- |
| `port` | required | the serial device |
| `baud` | `9600` | |

### `fake_registers`

`registers: {address: value}`; a write updates the table, so a readback
round-trips.

## Simulated plants

A plant is a link with inputs and outputs that integrate over the rig's
clock. [`sim_daq`](devices/drivers.md#sim_daq) reads its outputs,
[`sim_drive`](devices/drivers.md#sim_drive) drives its inputs. Physics and
worked examples: [Simulation](simulation.md).

### `sim_plant`

One input, one output.

| field | default | |
| --- | --- | --- |
| `model` | `lag` | `lag` (first order), `integrator` (a tank against a drain), `fopdt` (a lag with dead time) |
| `tau_s` | `10.0` | time constant (`lag`, `fopdt`) |
| `dead_s` | `0.0` | dead time (`fopdt`) |
| `gain` | `1.0` | output per unit input at rest |
| `leak` | `0.0` | drain rate (`integrator`) |
| `ambient` | `0.0` | where a lag rests with no input |
| `initial` | `0.0` | the output at start |
| `noise` | `0.0` | Gaussian noise on what is read, in the output's unit |
| `seed` | none | for a repeatable run |

Ports: input `input`, output `output`. A bare `sim_plant` does not say what
its output measures, so the `sim_daq` on it spells out `quantity` and
`unit`.

### `sim_furnace`

A tube furnace: heated zones in a row, coupled to their neighbours, losing
heat by conduction and radiation, with a sample coupled to one zone and
thermocouples that lag. Ports: inputs `heater1…N` (0–1), outputs
`zone1…N` and `sample` in °C, so a `sim_daq` on it needs no units.

| field | default | |
| --- | --- | --- |
| `zones` | `3` | |
| `power_w` | `2000` | one number, or one per zone |
| `capacity_j_per_k` | `5000` | a zone's thermal mass; one number or one per zone |
| `coupling_w_per_k` | `5.0` | between neighbouring zones |
| `loss_w_per_k` | `2.0` | conduction to ambient |
| `emissivity`, `area_m2` | `0.8`, `0.02` | radiation to ambient |
| `ambient_c` | `20.0` | |
| `sample_capacity_j_per_k`, `sample_coupling_w_per_k`, `sample_zone` | `800`, `4.0`, `2` | the sample and which zone it sits in |
| `sensor_lag_s` | `3.0` | the thermocouples' time constant |
| `initial_c` | ambient | every zone's temperature at start |
| `noise`, `seed` | `0.0`, none | |

## A board's buses

`i2c`, `spi`, `gpio`, `pwm` and `onewire`, with a `fake_*` each, come with
`flyball-linux` and are usually declared by a board profile rather than by
hand: [Boards and Linux I/O](boards.md).

## From a package

A package registers link tags of its own through the `flyball.configs`
entry point (`examples/humidity` adds `sim_humidity_chamber`, a mixing-model
chamber that is also a fake PWM chip); they are valid in a file the moment
it is installed. Writing one: [Config and build](../3-extending/device/config.md).
