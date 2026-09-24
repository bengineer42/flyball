# Supported drivers

Every driver a rig file can name with `driver:`, one section each: what it
is for, the link it sits on, its own fields, and an entry as it is written.
The fields under each are the driver's *own* config -- they sit flat beside
the [envelope](index.md) (`driver`, `label`, `poll_s`, `signals`, `inputs`).
Where they come from, and where else they show up, is
[the next page](generated.md).

Most bench instruments need one of the first four; a chip on a Raspberry Pi
one of the board drivers; a simulation the two `sim_*`. If none fits,
[Extending](../../3-extending/index.md) is one short class.

| driver | for | on a link | ships in | status |
| --- | --- | --- | --- | --- |
| [`scpi`](#scpi) | any text instrument: a query and/or a write template per signal | `visa`, `serial`, `fake_text` | `flyball-visa` | — |
| [`modbus`](#modbus) | PID controllers, MFCs, chillers, PLCs: a register per signal | `modbus_tcp`, `modbus_rtu`, `fake_registers` | `flyball-modbus` | — |
| [`qcodes`](#qcodes) | ~200 QCoDeS drivers, parameters as signals | its own | `flyball-qcodes[qcodes]` | — |
| [`pymeasure`](#pymeasure) | ~150 PyMeasure drivers, properties as signals | its own | `flyball-pymeasure[pymeasure]` | — |
| [`sim_daq`](#sim_daq) | read a simulated plant | `sim_plant`, or another package's own `MultiPlant` link such as `sim_furnace` | `flyball-sim` | — |
| [`sim_drive`](#sim_drive) | drive a simulated plant | `sim_plant`, or another package's own `MultiPlant` link such as `sim_furnace` | `flyball-sim` | — |
| [`i2c_table`](#i2c_table) | any register-mapped I²C chip | `i2c` | `flyball-linux` | — |
| [`sht4x`](#sht4x), [`sht4x_set`](#sht4x_set) | Sensirion humidity / temperature | `i2c` | `flyball-chips` | hardware-tested |
| [`ads1115`](#ads1115) | TI 16-bit ADC | `i2c` | `flyball-chips` | — |
| [`mcp3008`](#mcp3008) | Microchip 10-bit ADC | `spi` | `flyball-chips` | — |
| [`gpio_line`](#gpio_line) | a relay, a switch | `gpio` | `flyball-linux` | datasheet-checked |
| [`pwm_channel`](#pwm_channel) | a PWM output | `pwm` | `flyball-linux` | — |
| [`ds18b20`](#ds18b20) | 1-Wire thermometers | `onewire` | `flyball-linux` | — |
| [`sht31`](#sht31), [`htu21d`](#htu21d) | more Sensirion / TE humidity + temperature | `i2c` | `flyball-chips` | datasheet-checked |
| [`ms5611`](#ms5611) | TE barometric pressure | `i2c` | `flyball-chips` | known issue |
| [`bme280`](#bme280) | Bosch temperature / pressure / humidity | `i2c` | `flyball-chips` | partial |
| [`scd30`](#scd30), [`scd40`](#scd40) | Sensirion CO₂ + temperature + humidity | `i2c` | `flyball-chips` | partial |
| [`sgp30`](#sgp30), [`sgp40`](#sgp40) | Sensirion eCO₂ / TVOC / VOC index | `i2c` | `flyball-chips` | partial |
| [`ccs811`](#ccs811) | ams eCO₂ / TVOC | `i2c` | `flyball-chips` | partial |
| [`mhz19`](#mhz19) | Winsen CO₂ | `uart` | `flyball-chips` | datasheet-checked |
| [`ezo_ph`](#ezo_ph), [`ezo_ec`](#ezo_ec), [`ezo_orp`](#ezo_orp), [`ezo_do`](#ezo_do) | Atlas Scientific pH / EC / ORP / dissolved-oxygen circuits | `uart` | `flyball-chips` | partial (`ezo_ec`) |
| [`hx711`](#hx711) | a load cell amplifier | two `gpio_line`s | `flyball-chips` | partial |
| [`current_loop`](#current_loop) | a 4-20 mA instrument, over an existing ADC | `ads1115`/`mcp3008` | `flyball-linux` | partial |
| [`pulse_counter`](#pulse_counter) | a hall-effect flow meter | `gpio` | `flyball-linux` | datasheet-checked |
| [`dosing_pump`](#dosing_pump) | dispense a volume from a peristaltic pump | `pwm_channel`/`gpio_line` | `flyball-linux` | datasheet-checked |
| [`mcp4725`](#mcp4725) | a 0-10 V-class analog control signal (a VFD, a dimmable ballast, a damper) | `i2c` | `flyball-chips` | — |
| [`stepper`](#stepper) | a step/direction stepper motor: a motorized valve, damper or vent | two `gpio_line`s | `flyball-linux` | — |
| [`values`](#values) | numbers an operator enters, for other devices' inputs to follow | none | `flyball` | — |
| [`dual_pump_blender`](#dual_pump_blender) | [the humidity rig](https://bengineer42.github.io/humctrl/)'s split-range blender | `pwm`, `sim_humidity_chamber` | `examples/humidity` | hardware-tested |

`—` means not in `drivers-manifest.yaml` (the generic/wrapped-library drivers
and a few board-level ones aren't itemised there); `partial` and `unverified`
carry a caveat in the driver's own section below and in the manifest's
`links:` entries. Browsing what's available before wiring a rig:
`extensions/linux/drivers-manifest.yaml` (part number, manufacturer,
datasheet/hardware verification status, price, which application each
serves) and `extensions/linux/scripts/search_drivers.py` (filter it by
category, interface, unit or physical dimension -- units and dimensions
are read straight from each driver's own signals, not hand-maintained) --
or the MCP `search_drivers` tool, the same catalogue over a running server.

`GET /api/drivers` on a running runner lists exactly what *it* can build --
these plus anything from a `drivers/` directory or another installed
package.

**What a stop does** to a device is said in each driver's section: a stop
command, or an `off` its outputs declare, or nothing (`keep`: a stop leaves
the output as it is). A driver not named below declares none, so a stop
keeps its outputs unless the rig file's
[`stop:`](index.md#stop-what-a-stop-writes) gives a value -- today that is
every `modbus`, `i2c_table`, `qcodes` and `pymeasure` writable entry.

## When a driver has no value

What a driver yields for a value it has not got is its contract with the
rig ([no value](../../3-extending/model.md#no-value)): a signal left out
of a sample was not read this time; `invalid(reason)` is a good read of a
value that is not one (a no-value, which never counts toward
[`reads.fail_after`](index.md#reads)); `not_applicable(reason)` is a
quantity undefined now; a raise is the transport failing, which does. A
`None`, NaN or infinity in a sample is made `invalid` by the rig. Every
driver here follows it:

| driver | a value it has not got | a raise |
| --- | --- | --- |
| `scpi` | `9.91E37` → `invalid("not_a_number")`; `±9.9E37` → `invalid("overrange")`, low or high | no reply, a reply that is not a number |
| `modbus` | none: a register is always a number | a bus error |
| `qcodes`, `pymeasure` | a `None` or NaN from the instrument → `invalid` | the getter raising |
| `sim_daq` | `fail(signal)` → `invalid("sensor_failed")` on that channel | `fail(signal, raises=true)`: every read of it |
| `sim_drive`, `pwm_channel`, `mcp4725`, `dosing_pump`, `stepper` | n/a: write-only, or internal state | the bus |
| `i2c_table`, `ads1115`, `mcp3008`, `gpio_line`, `pulse_counter`, `hx711` | none: every read is a number | the bus; `hx711` a conversion not ready |
| `sht4x`, `sht4x_set`, `sht31`, `htu21d`, `scd30`, `scd40`, `sgp40`, `mhz19`, `ms5611` | none (humidity is cropped to 0-100 %, as the datasheets say) | a CRC failure, a short frame, a sensor not ready in time; in an `sht4x_set` one sensor's failure fails that read of the set |
| `bme280` | a BMP280 has no `humidity` signal at all | the bus |
| `ezo_*` | none | a `*` status reply, a malformed one |
| `ccs811` | no new result yet (`DATA_READY` clear) → nothing read | an error status, not in app mode |
| `sgp30` | its 15 s warm-up placeholders → nothing read (`pending`) | a CRC failure |
| `ds18b20` | none: the kernel's CRC line decides [Unverified: whether the kernel reports the 85 °C power-on value as a reading] | a failed CRC |
| `current_loop` | a NAMUR fault current → `invalid("ne43_low"/"ne43_high")`, low or high; 3.6-3.8 / 20.5-21 mA → the value, `at_limit` | the ADC's own failure |
| `dual_pump_blender` | `expected_humidity`: `not_applicable("no_flow")` with no flow, `invalid("supply")` while a supply has no value | the PWM bus |

A demand's reading is the value the rig committed (`readback: echo`)
unless the driver reads it back: `scpi` with a `query`, and `qcodes` /
`pymeasure` with a getter, declare theirs `sensed`. A driver that pushes a
quantised value back (`pwm_channel`, `mcp4725`, `gpio_line`, `i2c_table`,
`modbus`) computes it rather than reads it, so stays `echo`.

## Generic instruments

### `scpi`

One device per instrument, its signals declared as a table of queries and
write templates. `query` alone makes a signal `[RP]`, `write` alone `[W]`,
both `[RPW]`. Replies are parsed as a number (`+1.2E-3 V` → 0.0012).

| field | default | |
| --- | --- | --- |
| `link` | required | a [text link](../links.md#text-instruments), by name |
| `channels` | required | `{signal: {query?, write?, unit, quantity?, scale?, role?}}` -- `write` is a template with `{value}` (`"SOUR:VOLT {value:.3f}"`); `scale` multiplies a reply and divides a demand; `quantity` names the quantity when it differs from the signal's name; `role: setting` makes a signal with `write` a setting (a range, a mode) rather than a demand, so no controller can drive it -- only with `write` |
| `stop_command` | none | the text a stop sends (`"OUTP OFF"`, `"INP OFF"`): the instrument's own stop, run as its `stop` command. Omitted: no stop -- a stop leaves its outputs as they are, since the right string differs by instrument |

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
  stop_command: "OUTP OFF"      # a stop switches the output off
```

Adds two commands for bring-up, `write` and `query`, that send any text
outside the declared tree. An instrument that stops answering marks the
device `offline` after [`reads.fail_after`](index.md#reads) timeouts in a
row (default 3); polling goes on, retrying with backoff, and the next good
read clears it. SCPI's stand-ins for no number are no-values, not
numbers: `9.91E37` is `invalid("not_a_number")` and `±9.9E37`
`invalid("overrange")` on that side. A signal with both `query` and
`write` is read back from the instrument (`readback: sensed`). Replies that
are not a number need a `parse=` in Python: [Writing a sensor](../../3-extending/device/sensor.md#talking-to-an-instrument).

**Stop:** with `stop_command`, the `stop` command sends it, and `stop:`
values are refused on the device. Without it, each demand keeps unless the
rig file gives it a `stop:` value.

### `modbus`

One device per unit, a register per signal. `value = raw * scale`.

| field | default | |
| --- | --- | --- |
| `link` | required | a [register link](../links.md#register-instruments) |
| `registers` | required | `{signal: {address, kind?, unit, scale?, write?, role?}}` -- `kind` is `holding` (default), `input` or `coil`; `write: true` makes a `holding` register `[RPW]`, a demand (refused on `input`); `role: setting` makes a writable register a setting (a configuration register) rather than a demand, so no controller can drive it |
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
wrapper imports its library until a rig names it, so `import flyball` needs
nothing.

`instrument` is imported and then called with the file's arguments, so it
is held to the library's own instrument classes: a subclass of the
library's `Instrument` from under its driver package (below). Anything
else -- `os.system`, a function, a class from another package -- is refused
when the rig is validated, before anything is imported or called; a rig
edit through the API that names one is refused with 422. An in-house
driver package is allowed only by the machine running the rig:
`FLYBALL_INSTRUMENT_PACKAGES` in the runner's environment, comma-separated
package prefixes (`my_lab.instruments,qcodes_contrib_drivers`); a class
from one must still subclass the library's `Instrument`. No rig file key
widens it. `flyball rig check` checks the schema only and does not see
this; the runner does, and it needs the library installed to check.

### `qcodes`

| field | default | |
| --- | --- | --- |
| `instrument` | required | a dotted `qcodes.instrument.Instrument` subclass under `qcodes.instrument_drivers.` or `qcodes.instrument.`: `qcodes.instrument_drivers.Keithley.Keithley2450` |
| `instrument_name` | the device's name | the QCoDeS instrument's own `name` |
| `args`, `kwargs` | `[]`, `{}` | passed to the class |
| `link` | none | a transport by name, where the class takes one |
| `channels` | required | `{signal: {property, unit?, publish?, role?}}` -- `property` is the parameter, dotted for a submodule (`source.voltage`); `unit` overrides the parameter's own; `publish: true` polls it (needs a getter); `role: setting` makes a settable parameter a setting (a range, a mode) rather than a demand, so no controller can drive it |

```yaml
smu:
  driver: qcodes
  instrument: qcodes.instrument_drivers.Keithley.Keithley2450
  channels:
    voltage: { property: source.voltage, publish: true }
```

A gettable numeric parameter is a signal; a settable one is writable too,
and a demand unless it says `role: setting` (`readback: sensed` when it
also has a getter). A `None` from a getter is a reading with no value
(`invalid`), not an error.

### `pymeasure`

| field | default | |
| --- | --- | --- |
| `instrument` | required | a dotted `pymeasure.instruments.Instrument` subclass under `pymeasure.instruments.`: `pymeasure.instruments.keithley.Keithley2400` |
| `adapter` | required | `"GPIB::24"`, `"ASRL/dev/ttyUSB0"`, a VISA string |
| `kwargs` | `{}` | |
| `link` | none | a transport by name |
| `channels` | required | `{signal: {property, unit?, publish?, role?}}` -- the `measurement` / `control` / `setting` property; `unit` overrides what its docstring says (`"in volts"` → V); a settable property is a demand unless `role: setting` makes it a setting, which no controller can drive |

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
go bad and come back -- simulation only. A failed sensor reads
`invalid("sensor_failed")` (an open thermocouple the DAQ reports: the other
channels read on, and a controller on it freezes); `fail(signal, raises:
true)` instead makes every read of it raise, a dead bus, which takes the
device `offline` after its failure budget.

| field | default | |
| --- | --- | --- |
| `link` | required | a `sim_plant`, or another package's own `MultiPlant` link such as `examples/furnace`'s `sim_furnace` |
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
| `ports` | required | `{signal-path: port}`, or `{signal-path: {port, demand, quantity?, unit?, limits?}}`. `demand: input` (default) maps the signal linearly onto the port's 0–1 drive; `demand: output` declares the signal in the plant's own output unit and lets `commit` invert the plant's static model -- a heater commanded in °C. One signal per port: two on the same port are refused when the device is built |

**Stop:** a linear port (`demand: input`) declares `off` at `limits[0]`, 0 %
drive, and a stop writes it. A `demand: output` port (a setpoint in the
plant's unit) and a span across 0 declare none: a stop keeps them.

```yaml
heaters:
  driver: sim_drive
  link: tube
  ports: { heater1: heater1, heater2: heater2 }
```

## Board chips

Each sits on a board link ([Boards and Linux I/O](../boards.md)); `link` may
be the link's name or, with a `board:` in the file, `pin: LABEL` in its
place. Every one is tested to the byte against its link's fake. Most ship
in `flyball-chips` (protocol-level, OS-agnostic); a few -- `i2c_table`,
`gpio_line`, `pwm_channel`, `ds18b20`, `current_loop`, `pulse_counter`,
`dosing_pump`, `stepper` -- are board-level Linux drivers and ship in
`flyball-linux` instead. See the table above for which is which.

### `i2c_table`

Any register-mapped I²C chip without a driver of its own -- TMP117,
MCP9808, INA219, LM75.

| field | default | |
| --- | --- | --- |
| `link` | required | an `i2c` link |
| `address` | required | the chip's bus address |
| `registers` | required | `{signal: {address, length, signed?, byteorder?, shift?, scale?, offset?, unit, write?, role?}}` -- `write: true` makes a register a demand; `role: setting` makes a writable one a setting instead (a configuration register), which no controller can drive |

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

One line. As an output: a `[W]` signal `on` plus `on` / `off` commands,
which are refused while a controller drives `on` (put it in manual first).
As an input: a `[RP]` signal `level`.

| field | default | |
| --- | --- | --- |
| `link` | required | a `gpio` link, or `pin: GPIO18` from the board |
| `line` | required | |
| `direction` | `output` | `output` or `input` |
| `invert` | `false` | an active-low relay board or switch |
| `initial` | `false` | an output's level at start |
| `pull_up` | none | an input's bias: `true` up, `false` down, omitted as-is |

**Stop:** an output's `on` declares `off` at 0, and a stop writes it --
except with `invert: true`, where whether logical 0 is the load's off
depends on why it was inverted, so it declares none (keep). A direction or
select line, where 0 is a position rather than off, should say
`stop: {on: keep}`.

### `pwm_channel`

One PWM output, a `[W]` signal `drive`, and an `off` command (duty to zero,
channel disabled) that is refused while a controller drives `drive`.

| field | default | |
| --- | --- | --- |
| `link` | required | a `pwm` link, or `pin: PWM0` |
| `channel` | required | |
| `frequency_hz` | `1000` | |
| `invert` | `false` | |
| `unit`, `quantity`, `span` | none | omitted, `drive` is the duty itself (0–1). Given, `drive` is set in `unit` (say °C) and `span: [lo, hi]` maps it linearly onto 0–100 % -- a static feedforward inside the device, so a controller may drive it with `feedforward: {type: identity}` |

**Stop:** `drive` declares `off` at 0 % duty (0, or `span[0]`), and a stop
writes it; the channel stays enabled. The `off` command, which also
disables the channel, is not the stop. With `invert: true`, or a span that
straddles 0 (full reverse at one end: an H-bridge, a Peltier), it declares
none, and a stop keeps `drive`. A fan or a coolant pump that must keep
running after a stop says `stop: {drive: 1}` or `stop: {drive: keep}`.

### `ds18b20`

A 1-Wire thermometer of the `w1_therm` family, in °C.

| field | default | |
| --- | --- | --- |
| `link` | required | an `onewire` link |
| `device` | required | the probe's id under `/sys/bus/w1/devices` (`28-0316…`) |

### `sht31`

Sensirion SHT30/31/35: `humidity` and `temperature`, both `[RP]`. Same
shape as `sht4x`, a different command/CRC family -- not a register table.

| field | default | |
| --- | --- | --- |
| `link` | required | an `i2c` link |
| `address` | `0x44` | `0x45` on the -B variant |
| `precision` | `high` | `high`, `medium`, `low` |

### `htu21d`

TE Connectivity HTU21D(F) / Silicon Labs Si7021: `humidity` and
`temperature`, both `[RP]`. No-hold-master trigger, poll-until-ready reads.

| field | default | |
| --- | --- | --- |
| `link` | required | an `i2c` link |
| `address` | `0x40` | fixed -- no address pin |

### `ms5611`

TE MS5611 barometric pressure: `pressure` (Pa) and `temperature`, both
`[RP]`. PROM calibration read once, then a timed ADC conversion per sample.

| field | default | |
| --- | --- | --- |
| `link` | required | an `i2c` link |
| `address` | `0x77` | `0x76` on the CSB-low variant |

!!! note "Known issue"
    The PROM's CRC-4 checksum is read but not verified -- a corrupted
    calibration block would be trusted silently rather than raising.
    Whether to implement the check is still open.

### `bme280`

Bosch BME280/BMP280: `temperature`, `pressure`, and (BME280 only)
`humidity`, all `[RP]`. Reads the calibration block once and applies
Bosch's own polynomial compensation -- not `i2c_table`, the raw registers
don't convert linearly.

| field | default | |
| --- | --- | --- |
| `link` | required | an `i2c` link |
| `address` | `0x76` | `0x77` on SDO-high |
| `has_humidity` | `true` | `false` for a BMP280 (no humidity registers) |

### `scd30`

Sensirion SCD30: `co2` (ppm), `humidity`, `temperature`, all `[RP]` from
one transaction. I²C mode only -- the chip's alternative Modbus-over-UART
mode isn't wired up.

| field | default | |
| --- | --- | --- |
| `link` | required | an `i2c` link |
| `address` | `0x61` | fixed |
| `pressure_mbar` | `0` | ambient pressure for the chip's own compensation; `0` turns it off |
| `sleep` | `true` | wait for the chip's data-ready flag before each read (up to its timeout) rather than read whatever it last measured |

The chip runs in continuous mode at its own 2 s period; there is no
`interval_s` here -- pace it with the device's `poll_s`.

### `scd40`

Sensirion SCD40/SCD41: `co2` (ppm), `humidity`, `temperature`, all `[RP]`.
Same three-value CRC family as `scd30`, a different command set.

| field | default | |
| --- | --- | --- |
| `link` | required | an `i2c` link |
| `address` | `0x62` | fixed |
| `variant` | `scd40` | `scd40` or `scd41` -- SCD41 adds a single-shot mode |
| `low_power` | `false` | periodic measurement every 30 s instead of 5 s |
| `single_shot` | `false` | SCD41 only: measure on demand at each read rather than periodically; refused on an SCD40 |
| `sleep` | `true` | wait for the chip's data-ready flag before each read (up to the period) rather than read whatever it last measured |

### `sgp30`

Sensirion SGP30: `co2eq` (ppm) and `tvoc` (ppb), both `[RP]`. Needs a
periodic baseline (get/set) for long-term accuracy. The driver class has
`get_baseline`/`set_baseline`; a baseline read back earlier can be restored
at startup with the `baseline` field, so the chip need not settle from
scratch on every power cycle. The `baseline` command reads the current
baseline back out, to save for that field. For its first 15 s its outputs
are fixed placeholders (400 ppm, 0 ppb): the chip is measured every poll,
as the algorithm needs, but nothing is read until they are over -- the
signals stay `pending`.

| field | default | |
| --- | --- | --- |
| `link` | required | an `i2c` link |
| `address` | `0x58` | fixed |
| `baseline` | none | `[co2eq, tvoc]` IAQ baseline words to restore at startup, as read back earlier from `get_baseline`; omitted lets the chip's own algorithm re-settle from cold |

### `sgp40`

Sensirion SGP40: `voc_raw` (dimensionless), `[RP]`. Raw signal only -- no
VOC-index algorithm. The chip takes a humidity/temperature compensation
input per read; `humidity_source`/`temperature_source` name the signal
address to read that compensation input from on each read. Omitting either
falls back to the datasheet's fixed default (50 %RH, 25 °C) for that input.

| field | default | |
| --- | --- | --- |
| `link` | required | an `i2c` link |
| `address` | `0x59` | fixed |
| `humidity_source` | none | signal address to read humidity compensation from each read; omit for the fixed 50 %RH default |
| `temperature_source` | none | signal address to read temperature compensation from each read; omit for the fixed 25 °C default |

### `ccs811`

ams/ScioSense CCS811: `co2eq` (ppm), `tvoc` (ppb), both `[RP]`. Runs a
mandatory boot/app-start sequence before its first read. A poll while no
new result is ready (`STATUS` without `DATA_READY`) reads nothing.

| field | default | |
| --- | --- | --- |
| `link` | required | an `i2c` link |
| `address` | `0x5A` | `0x5B` on the ADDR-high variant |

### `mhz19`

Winsen MH-Z19(B): `co2` (ppm), `[RP]`. Fixed 9-byte binary frames, not
ASCII -- a request/reply pair per read, checksummed.

| field | default | |
| --- | --- | --- |
| `link` | required | a `uart` link, 9600 8N1 |

### `ezo_ph`

Atlas Scientific EZO-pH circuit: `ph`, `[RP]`. ASCII command/response,
`\r`-terminated, a ~1 s wait per reading. Handles the circuit's
default-enabled `*OK` acknowledgement frame -- the shared shape every
`ezo_*` driver below builds on.

| field | default | |
| --- | --- | --- |
| `link` | required | a `uart` link, 38400 8N1 |

### `ezo_ec`

Atlas Scientific EZO-EC circuit: conductivity, `[RP]`. Decodes the
factory-default CSV reply (`EC,TDS,SAL,SG`) per the datasheet's
quick-reference table. [Unverified] the same datasheet's own worked
example shows a bare single value instead -- looks like a stale example
from an older revision; worth checking against real hardware before
trusting the CSV assumption.

| field | default | |
| --- | --- | --- |
| `link` | required | a `uart` link, 38400 8N1 |

### `ezo_orp`

Atlas Scientific EZO-ORP circuit: a single mV reading, `[RP]`.

| field | default | |
| --- | --- | --- |
| `link` | required | a `uart` link, 38400 8N1 |

### `ezo_do`

Atlas Scientific EZO-DO circuit: dissolved oxygen in mg/L, `[RP]`.
Decodes the factory-default single-value reply; raises rather than
guessing if the circuit was reconfigured to also report % saturation
(a comma in the reply).

| field | default | |
| --- | --- | --- |
| `link` | required | a `uart` link, 38400 8N1 |

### `hx711`

Avia HX711 load-cell amplifier: `weight`, `[RP]`. Bit-banged 2-wire
clock/data, not I²C or SPI -- two raw GPIO lines, not a shared bus.

| field | default | |
| --- | --- | --- |
| `link` | required | a `gpio` link |
| `clock_line`, `data_line` | required | `gpio_line`-style line numbers: PD_SCK (host drives) and DOUT (chip drives) |
| `gain` | `a128` | channel and gain, `a128`, `b32` or `a64` -- the chip's three pulse-count selections |
| `scale`, `offset` | `1.0`, `0.0` | `weight = raw * scale + offset` -- calibrate per load cell: tare at zero, then a known reference weight |

Timing-sensitive: decoded per datasheet, but no fake can meaningfully
exercise real GPIO bit-bang timing -- only the decode/pulse-count logic is
tested.

### `current_loop`

A 4-20 mA instrument (an industrial O₂/DO analyser, a pressure
transmitter) read over an existing `ads1115` or `mcp3008` channel, not a
new bus -- it composes one, converting mA through a sense resistor into
engineering units. Below ~3.6 mA or above ~21 mA is a wiring fault, not a
real reading (the NAMUR NE43 convention): that channel reads `invalid`
(`ne43_low` or `ne43_high`, with its side), never a clamped value and never
a raise -- the ADC was read, so its other channels read on and the device
stays online. Between 3.6 and 3.8 mA, or 20.5 and 21 mA, the transmitter is
pinned at an end of its range: the value is reported with the caveat
`at_limit`. An `alarm` band on the signal raises `band_unknown` on the
fault ([Bands](index.md#a-banded-signal-with-no-value)). [Unverified: the
NE43 thresholds are the commonly quoted ones; the NAMUR text was not
consulted.]

| field | default | |
| --- | --- | --- |
| `adc` | required | an `ads1115` or `mcp3008` config |
| `channels` | required | `{signal: {channel, unit?, resistor_ohms?, scale?, offset?}}` -- `resistor_ohms` defaults to `250`, `unit` to `1`, and `value = mA * scale + offset` with `scale` `1.0`, `offset` `0.0` |

### `pulse_counter`

A hall-effect flow meter (YF-S201-class): `rate` (L/min) and `count`
(cumulative pulses), both `[RP]`. Native `gpiod` edge-event detection and
debounce -- no hand-rolled polling loop.

| field | default | |
| --- | --- | --- |
| `link` | required | a `gpio` link |
| `line` | required | |
| `pulses_per_litre` | required | the sensor's own calibration constant, e.g. 450 for a YF-S201 |
| `debounce_s` | `0` | passed straight to `gpiod`'s native debounce |
| `pull_up` | omitted | the line's bias: `true` pulls up, `false` pulls down, omitted leaves it as the board has it |

### `dosing_pump`

A peristaltic pump, PWM-driven DC or a relay: a `dispense(volume_ml)`
command on top of an existing `pwm_channel` or `gpio_line`, converting a
volume to a run duration from one calibration point. Always stops the
pump on the way out, including on an error mid-dispense. A dispense runs
off the rig lock: polling and control carry on during the dose, and the
`stop` command cuts the pump at once and ends the dispense, crediting
`dispensed_ml` with what ran. The dose is timed on the rig's clock, so a
scaled or stepped sim doses in its own time. The pump underneath is the
dosing pump's own, not a device of the rig, so nothing else can drive it
while it doses. Stepper-driven
pumps (step/direction) aren't supported directly here -- pair a `stepper`
with your own dispense logic instead.

| field | default | |
| --- | --- | --- |
| `pump` | required | a `pwm_channel` or `gpio_line` config |
| `ml_per_s` | required | the pump's rate at full drive (PWM) or while on (relay) |
| `max_dispense_ml` | none | an optional per-call cap |
| `drive_fraction` | `1.0` | the duty to run a `pwm_channel` pump at during a dispense; refused on a `gpio_line` pump unless left at `1.0` |

**Stop:** the `stop` command is the device's stop: a rig stop cancels a
dispense in progress and runs it, cutting the pump. `stop:` values are
refused on the device.

### `mcp4725`

Microchip MCP4725: single-channel, 12-bit buffered I²C DAC. The write-side
mirror of an analog input like `ads1115`/`mcp3008` -- drives a 0-1
fraction of full scale into an external op-amp stage a rig uses for a
0-10 V (or similar) control signal: a VFD speed reference, a dimmable
ballast, a damper actuator. Only the chip's "Fast Mode" write is used
(no register-address byte, unlike `i2c_table`'s chips -- it needs its own
driver). With `unit` and `span` (as `pwm_channel` has) the signal is
commanded directly in engineering units instead of a bare fraction.

| field | default | |
| --- | --- | --- |
| `link` | required | an `i2c` link |
| `address` | `0x60` | `0x61` on the -A0T variant |
| `unit`, `quantity`, `span` | none | omitted, `drive` is the fraction itself (0-1). Given, `drive` is set in `unit` and `span: [lo, hi]` maps it linearly onto 0-100 % -- the same rule as `pwm_channel` |

**Stop:** `drive` declares no `off`: 0 V is a setpoint for a positioner or
a VFD, not off. The device's stop is its `power_down` command, which puts
the chip in power-down with its output pulled to ground through 1 kΩ
(`PD = 01`); the next demand powers it up again. `stop:` values are refused
on the device.

### `stepper`

A step/direction stepper motor -- the interface almost every real driver
IC exposes (A4988, DRV8825, TMC-series), not raw phase bit-banging, which
would be exactly the kind of fragile Python timing `hx711` is already
flagged for. A motorized valve, damper, vent or linear actuator. A
`move(steps)` command (not `move_to(position)` -- no homing/limit-switch
story to trust an absolute target against) clocks out a pulse train at
`steps_per_s`, always leaving the driver IC with its direction settled and
`enable_line` released, even on an error mid-move. A move runs off the
rig lock, its gaps between pulses timed on the rig's clock: the `stop`
command ends it after the pulse in progress and releases `enable_line`.

| field | default | |
| --- | --- | --- |
| `link` | required | a `gpio` link |
| `step_line`, `direction_line` | required | |
| `steps_per_s` | required | |
| `enable_line` | none | driven active for the move's duration, released after |
| `enable_active_low` | `true` | the common driver-IC convention |
| `steps_per_unit` | none | lets `move()` take engineering units (degrees, mm) instead of raw steps |
| `pulse_width_s` | `0.0005` | how long the step line is held high per pulse |

**Stop:** the `stop` command is the device's stop: a rig stop cancels a
move in progress and runs it, which releases `enable_line`. Without an
`enable_line` it only ends the move, and the coils stay as they were.
`stop:` values are refused on the device.

`position` -- the raw step count -- is `[R]` only (readable on demand,
never published/recorded by default): internal plumbing for the move
command, not a quantity a rig cares to trend. A rig-file `signals:`
entry cannot widen this to `[RP]` unless the driver names it a
[ceiling](../../7-reference/rig-file.md) -- `stepper` doesn't, on
purpose, so upgrading it needs a driver code change, not a config
change.

## Built in

### `values`

Numbers an operator enters, for other devices' [inputs](index.md#binding-one-device-to-another)
to follow: a supply humidity measured by hand, a setpoint for a batch, a
calibration factor. Each is a signal of the device -- a `setting`, `rpw` --
published from build with its `initial`, so an input bound to it is never
`pending`. An input that never changes binds to a plain number instead.

```yaml
devices:
  bench:
    driver: values
    label: Bench values
    values:
      dry_supply: { initial: 36.5, unit: "%", label: Dry supply humidity, limits: [0, 100] }
      wet_supply: { initial: 88.5, unit: "%", label: Wet supply humidity }
  blender:
    inputs: { dry: bench.dry_supply, wet: bench.wet_supply }
```

| field | | |
| --- | --- | --- |
| `values` | `{name: entry}` | one signal per entry |
| `values.<name>.initial` | number | its value from build, finite |
| `values.<name>.unit` | string | the unit symbol (`%`, `°C`, `L/min`); omit for none |
| `values.<name>.quantity` | string | what it is (`humidity`); default: the entry's name |
| `values.<name>.label` | string | the display text |
| `values.<name>.limits` | `[lo, hi]` | what a write is clamped to |

- **Written like any writable signal**: `PUT /api/signals/bench.dry_supply`
  (`operate`), a program's `set` step, the device page. The rig clamps it to
  its `limits`, logs each write as a `value_written` event naming who wrote
  it and the value before, and records it when a recording is running.
- **Never stale.** Nothing reads it from hardware, so nothing judges it
  stale.
- **A stop leaves it alone.** It has no demands; the stop report does not
  list it, and an operator can correct a value while the rig is stopped.
- **Kept across restarts.** The last written value, who wrote it and when,
  its unit and the `initial` in force are kept in the store (the runner
  always opens one, recording or not). On the next start it is restored --
  a `value_restored` event -- as long as the rig file's `initial` for it is
  what it was when the value was written; a changed `initial` means the file
  was edited since, and the file wins. A changed `unit` also leaves the
  file's `initial` in force, and raises `value_not_restored` on the signal
  until it is written again.
- **Where it came from**: the device page shows each value's source --
  "rig file", "restored, written by X at T", or written in this run --
  from `sources` on [`GET /api/devices/{name}`](../../4-server/api.md).

## From an application

### `dual_pump_blender`

The humidity rig's actuator: two pumps on one PWM chip, blended so that one
`humidity` demand becomes a dry-line and a wet-line flow. The genuine driver
runs on hardware (`pwm`) and on the simulated chamber alike. Its fields
(`dry`, `wet`, `blend_flow`, `frequency_hz`), its two inputs (`dry` and
`wet`, the supply lines' humidity: a sensor's address or a number each) and
the physics are in
[the humidity book](https://bengineer42.github.io/humctrl/3-devices/blender/).
Its stop is its `stop` command, both pumps off at once; `stop:` values are
refused on it. The class is `examples/humidity/src/humidity/blender.py`,
the worked example of a composite device in
[Writing an actuator](../../3-extending/device/actuator.md).

## Not yet

EPICS / OPC UA (the pushed-device path exists, no adapter), NI-DAQmx /
LabJack, vendor packages with the query tables filled in (`flyball-keithley`
-- the entry-point discovery exists, the packages do not), an instrument's
own error queue as a condition. The wrapped libraries cover most bench kit
until then.
