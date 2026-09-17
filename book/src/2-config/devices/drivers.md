# Supported drivers

Every driver a rig file can name with `driver:`, one section each: what it
is for, the link it sits on, its own fields, and an entry as it is written.
The fields under each are the driver's *own* config -- they sit flat beside
the [envelope](index.md) (`driver`, `label`, `poll_s`, `signals`, `bound`)
or under `config:`. Where they come from, and where else they show up, is
[the next page](generated.md).

Most bench instruments need one of the first four; a chip on a Raspberry Pi
one of the board drivers; a simulation the two `sim_*`. If none fits,
[Extending](../../3-extending/index.md) is one short class.

| driver | for | on a link | ships in |
| --- | --- | --- | --- |
| [`scpi`](#scpi) | any text instrument: a query and/or a write template per signal | `visa`, `serial`, `fake_text` | `flyball` |
| [`modbus`](#modbus) | PID controllers, MFCs, chillers, PLCs: a register per signal | `modbus_tcp`, `modbus_rtu`, `fake_registers` | `flyball` |
| [`qcodes`](#qcodes) | ~200 QCoDeS drivers, parameters as signals | its own | `flyball[qcodes]` |
| [`pymeasure`](#pymeasure) | ~150 PyMeasure drivers, properties as signals | its own | `flyball[pymeasure]` |
| [`sim_daq`](#sim_daq) | read a simulated plant | `sim_plant`, `sim_furnace` | `flyball` |
| [`sim_drive`](#sim_drive) | drive a simulated plant | `sim_plant`, `sim_furnace` | `flyball` |
| [`i2c_table`](#i2c_table) | any register-mapped I²C chip | `i2c` | `flyball-linux` |
| [`sht4x`](#sht4x), [`sht4x_set`](#sht4x_set) | Sensirion humidity / temperature | `i2c` | `flyball-linux` |
| [`ads1115`](#ads1115) | TI 16-bit ADC | `i2c` | `flyball-linux` |
| [`mcp3008`](#mcp3008) | Microchip 10-bit ADC | `spi` | `flyball-linux` |
| [`gpio_line`](#gpio_line) | a relay, a switch | `gpio` | `flyball-linux` |
| [`pwm_channel`](#pwm_channel) | a PWM output | `pwm` | `flyball-linux` |
| [`ds18b20`](#ds18b20) | 1-Wire thermometers | `onewire` | `flyball-linux` |
| [`dual_pump_blender`](#dual_pump_blender) | [the humidity rig](https://bengineer42.github.io/flyball/humidity/)'s split-range blender | `pwm`, `sim_humidity_chamber` | `examples/humidity` |

`GET /api/drivers` on a running daemon lists exactly what *it* can build --
these plus anything from a `drivers/` directory or another installed
package.

## Generic instruments

### `scpi`

One device per instrument, its signals declared as a table of queries and
write templates. `query` alone makes a signal `[RP]`, `write` alone `[W]`,
both `[RPW]`. Replies are parsed as a number (`+1.2E-3 V` → 0.0012).

| field | default | |
| --- | --- | --- |
| `link` | required | a [text link](../links.md#text-instruments), by name |
| `channels` | required | `{signal: {query?, write?, unit, quantity?, scale?}}` -- `write` is a template with `{value}` (`"SOUR:VOLT {value:.3f}"`); `scale` multiplies a reply and divides a demand; `quantity` names the quantity when it differs from the signal's name |

```yaml
psu:
  driver: scpi
  link: psu
  poll_s: 1
  channels:
    set_voltage:    { write: "SOUR:VOLT {value:.3f}", unit: V }     # [W]
    output_voltage: { query: "MEAS:VOLT?", unit: V }                # [RP]
  signals:
    set_voltage: { limits: [0, 30] }
```

Adds two commands for bring-up, `write` and `query`, that send any text
outside the declared tree. An instrument that stops answering marks the
device `offline` until the next good read; polling goes on. Replies that
are not a number need a `parse=` in Python: [Writing a sensor](../../3-extending/device/sensor.md#talking-to-an-instrument).

### `modbus`

One device per unit, a register per signal. `value = raw * scale`.

| field | default | |
| --- | --- | --- |
| `link` | required | a [register link](../links.md#register-instruments) |
| `registers` | required | `{signal: {address, kind?, unit, scale?, write?}}` -- `kind` is `holding` (default), `input` or `coil`; `write: true` makes a `holding` register `[RPW]` (refused on `input`) |
| `unit_id` | `1` | the Modbus unit (slave) id |

```yaml
chiller:
  driver: modbus
  link: chiller
  registers:
    temperature: { address: 100, scale: 0.1, unit: "°C" }
    setpoint:    { address: 101, scale: 0.1, unit: "°C", write: true }
```

## Wrapped instrument libraries

Two Python libraries carry hundreds of drivers between them; one wrapper
each turns any of them into a device. The library owns the connection, so
the link fakes are not involved -- test against the library's own
simulated instruments. Their calls block like any polled device's. Neither
wrapper imports its library until built, so `import flyball` needs nothing.

### `qcodes`

| field | default | |
| --- | --- | --- |
| `instrument` | required | a dotted class in an installed package: `qcodes.instrument_drivers.Keithley.Keithley2450` |
| `instrument_name` | the device's name | the QCoDeS instrument's own `name` |
| `args`, `kwargs` | `[]`, `{}` | passed to the class |
| `link` | none | a transport by name, where the class takes one |
| `channels` | required | `{signal: {property, unit?, publish?}}` -- `property` is the parameter, dotted for a submodule (`source.voltage`); `unit` overrides the parameter's own; `publish: true` polls it (needs a getter) |

```yaml
smu:
  driver: qcodes
  instrument: qcodes.instrument_drivers.Keithley.Keithley2450
  channels:
    voltage: { property: source.voltage, publish: true }
```

A gettable numeric parameter is a signal; a settable one is writable too.

### `pymeasure`

| field | default | |
| --- | --- | --- |
| `instrument` | required | `pymeasure.instruments.keithley.Keithley2400` |
| `adapter` | required | `"GPIB::24"`, `"ASRL/dev/ttyUSB0"`, a VISA string |
| `kwargs` | `{}` | |
| `link` | none | a transport by name |
| `channels` | required | `{signal: {property, unit?, publish?}}` -- the `measurement` / `control` / `setting` property; `unit` overrides what its docstring says (`"in volts"` → V) |

```yaml
smu:
  driver: pymeasure
  instrument: pymeasure.instruments.keithley.Keithley2400
  adapter: "GPIB::24"
  channels:
    voltage: { property: voltage, publish: true }
    bias:    { property: source_voltage }
```

## Simulation

### `sim_daq`

Reads chosen outputs of a [simulated plant](../links.md#simulated-plants) as
`[RP]` signals. Commands `fail(signal)` / `restore(signal)` make a reading
go bad and come back -- simulation only.

| field | default | |
| --- | --- | --- |
| `link` | required | a `sim_plant` or `sim_furnace` |
| `ports` | required | `{signal-path: port}` or `{signal-path: {port, quantity, unit, limits?}}`; the long form when the plant does not say what a port measures (a bare `sim_plant`). A dotted path (`dry.humidity`) puts the signal in a namespace, so a simulated device can mirror a real one's addresses |

```yaml
furnace:
  driver: sim_daq
  link: tube
  poll_s: 1
  ports: { zone1: zone1, zone2: zone2, sample: sample }      # the furnace knows its units
probe:
  driver: sim_daq
  link: plant
  ports: { temperature: { port: output, quantity: temperature, unit: "°C" } }
```

### `sim_drive`

Drives chosen inputs of a plant from `[W]` signals.

| field | default | |
| --- | --- | --- |
| `link` | required | the same plant |
| `ports` | required | `{signal-path: port}`, or `{signal-path: {port, demand, quantity?, unit?, limits?}}`. `demand: input` (default) maps the signal linearly onto the port's 0–1 drive; `demand: output` declares the signal in the plant's own output unit and lets `commit` invert the plant's static model -- a heater commanded in °C |

```yaml
heaters:
  driver: sim_drive
  link: tube
  ports: { heater1: heater1, heater2: heater2 }
```

## Board chips (`flyball-linux`)

Each sits on a board link ([Boards and Linux I/O](../boards.md)); `link` may
be the link's name or, with a `board:` in the file, `pin: LABEL` in its
place. Every one is tested to the byte against its link's fake.

### `i2c_table`

Any register-mapped I²C chip without a driver of its own -- TMP117,
MCP9808, INA219, LM75.

| field | default | |
| --- | --- | --- |
| `link` | required | an `i2c` link |
| `address` | required | the chip's bus address |
| `registers` | required | `{signal: {address, length, signed?, byteorder?, shift?, scale?, offset?, unit, write?}}` |

```yaml
board_temp:
  driver: i2c_table
  link: i2c1
  address: 0x48
  registers:
    temperature: { address: 0, length: 2, signed: true, scale: 0.0078125, unit: "°C" }
```

### `sht4x`

One Sensirion SHT40/41/45: `humidity` and `temperature`, both `[RP]`.

| field | default | |
| --- | --- | --- |
| `link` | required | an `i2c` link |
| `address` | `0x44` | |
| `precision` | `high` | `high`, `medium`, `low` -- conversion time against resolution |

### `sht4x_set`

Several SHT4x on one bus, each its own namespace (`hum_sensors.dry.humidity`),
read in one poll.

| field | default | |
| --- | --- | --- |
| `link` | required | |
| `sensors` | required | `{name: {address}}` |
| `precision` | `high` | |

```yaml
hum_sensors:
  driver: sht4x_set
  link: i2c1
  poll_s: 1
  sensors: { chamber: { address: 0x44 }, dry: { address: 0x45 }, wet: { address: 0x46 } }
```

### `ads1115`

TI 16-bit ADC, four single-ended channels.

| field | default | |
| --- | --- | --- |
| `link` | required | an `i2c` link |
| `address` | `0x48` | |
| `gain` | `1` | PGA gain: `2/3`, `1`, `2`, `4`, `8` or `16` |
| `channels` | required | `{signal: {channel, scale?, unit?}}` -- volts unless `scale` and `unit` say otherwise |

```yaml
pressure_adc:
  driver: ads1115
  link: i2c1
  channels: { pressure: { channel: 0, scale: 25.0, unit: kPa } }
```

### `mcp3008`

Microchip 10-bit ADC, eight channels, over SPI.

| field | default | |
| --- | --- | --- |
| `link` | required | an `spi` link |
| `vref` | `3.3` | the reference voltage on VREF |
| `channels` | required | `{signal: {channel, scale?, unit?}}` |

### `gpio_line`

One line. As an output: a `[W]` signal `on` plus `on` / `off` commands. As
an input: a `[RP]` signal `level`.

| field | default | |
| --- | --- | --- |
| `link` | required | a `gpio` link, or `pin: GPIO18` from the board |
| `line` | required | |
| `direction` | `output` | `output` or `input` |
| `invert` | `false` | an active-low relay board or switch |
| `initial` | `false` | an output's level at start |
| `pull_up` | none | an input's bias: `true` up, `false` down, omitted as-is |

### `pwm_channel`

One PWM output, a `[W]` signal `drive`.

| field | default | |
| --- | --- | --- |
| `link` | required | a `pwm` link, or `pin: PWM0` |
| `channel` | required | |
| `frequency_hz` | `1000` | |
| `invert` | `false` | |
| `unit`, `quantity`, `span` | none | omitted, `drive` is the duty itself (0–1). Given, `drive` is set in `unit` (say °C) and `span: [lo, hi]` maps it linearly onto 0–100 % -- a static feedforward inside the device, so a controller may drive it with `feedforward: {tag: setpoint}` |

### `ds18b20`

A 1-Wire thermometer of the `w1_therm` family, in °C.

| field | default | |
| --- | --- | --- |
| `link` | required | an `onewire` link |
| `device` | required | the probe's id under `/sys/bus/w1/devices` (`28-0316…`) |

## From an application

### `dual_pump_blender`

The humidity rig's actuator: two pumps on one PWM chip, blended so that one
`humidity` demand becomes a dry-line and a wet-line flow. The genuine driver
runs on hardware (`pwm`) and on the simulated chamber alike. Its fields
(`dry`, `wet`, `blend_flow`, `supply`, `frequency_hz`) and the physics are in
[the humidity book](https://bengineer42.github.io/flyball/humidity/3-devices/blender/); the class is `examples/humidity/src/humidity/blender.py`,
the worked example of a composite device in
[Writing an actuator](../../3-extending/device/actuator.md).

## Not yet

EPICS / OPC UA (the pushed-device path exists, no adapter), NI-DAQmx /
LabJack, vendor packages with the query tables filled in (`flyball-keithley`
-- the entry-point discovery exists, the packages do not), an instrument's
own error queue as a condition. The wrapped libraries cover most bench kit
until then.
