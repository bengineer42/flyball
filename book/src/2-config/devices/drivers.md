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
| [`sht31`](#sht31), [`htu21d`](#htu21d) | more Sensirion / TE humidity + temperature | `i2c` | `flyball-linux` |
| [`ms5611`](#ms5611) | TE barometric pressure | `i2c` | `flyball-linux` |
| [`bme280`](#bme280) | Bosch temperature / pressure / humidity | `i2c` | `flyball-linux` |
| [`bme680`](#bme680) | Bosch temperature / pressure / humidity / gas | `i2c` | `flyball-linux` |
| [`scd30`](#scd30), [`scd40`](#scd40) | Sensirion CO₂ + temperature + humidity | `i2c` | `flyball-linux` |
| [`sgp30`](#sgp30), [`sgp40`](#sgp40) | Sensirion eCO₂ / TVOC / VOC index | `i2c` | `flyball-linux` |
| [`ccs811`](#ccs811) | ams eCO₂ / TVOC | `i2c` | `flyball-linux` |
| [`mhz19`](#mhz19) | Winsen CO₂ | `uart` | `flyball-linux` |
| [`ezo_ph`](#ezo_ph), [`ezo_ec`](#ezo_ec), [`ezo_orp`](#ezo_orp), [`ezo_do`](#ezo_do) | Atlas Scientific pH / EC / ORP / dissolved-oxygen circuits | `uart` | `flyball-linux` |
| [`hx711`](#hx711) | a load cell amplifier | two `gpio_line`s | `flyball-linux` |
| [`current_loop`](#current_loop) | a 4-20 mA instrument, over an existing ADC | `ads1115`/`mcp3008` | `flyball-linux` |
| [`pulse_counter`](#pulse_counter) | a hall-effect flow meter | `gpio` | `flyball-linux` |
| [`dosing_pump`](#dosing_pump) | dispense a volume from a peristaltic pump | `pwm_channel`/`gpio_line` | `flyball-linux` |
| [`mcp4725`](#mcp4725) | a 0-10 V-class analog control signal (a VFD, a dimmable ballast, a damper) | `i2c` | `flyball-linux` |
| [`stepper`](#stepper) | a step/direction stepper motor: a motorized valve, damper or vent | two `gpio_line`s | `flyball-linux` |
| [`dual_pump_blender`](#dual_pump_blender) | [the humidity rig](https://bengineer42.github.io/flyball/humidity/)'s split-range blender | `pwm`, `sim_humidity_chamber` | `examples/humidity` |

Browsing what's available before wiring a rig: `linux/drivers-manifest.yaml`
(part number, manufacturer, verification status, price, which application
each serves) and `linux/scripts/search_drivers.py` (filter it by category,
interface, unit or physical dimension -- units and dimensions are read
straight from each driver's own signals, not hand-maintained) -- or the
MCP `search_drivers` tool, the same catalogue over a running server.

`GET /api/drivers` on a running runner lists exactly what *it* can build --
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

### `bme680`

Bosch BME680: `temperature`, `pressure`, `humidity`, `gas_resistance`, all
`[RP]`. Its compensation formula genuinely differs from BME280's -- not
reused. The gas channel runs a timed heater profile before each reading;
a reading taken before the heater is stable raises rather than returning a
silently-wrong resistance.

| field | default | |
| --- | --- | --- |
| `link` | required | an `i2c` link |
| `address` | `0x76` | `0x77` on SDO-high |
| `osrs_t`, `osrs_p`, `osrs_h` | `2`, `4`, `2` | oversampling per channel: `0` (skip), `1`, `2`, `4`, `8` or `16` |
| `gas_heater_c`, `gas_wait_ms` | `320`, `150` | the gas-sensing heater profile used on every read, 0-400 °C and 0-4032 ms |

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
`get_baseline`/`set_baseline`, but nothing exposes them yet: there is no
rig-file field or device command for a saved baseline, so the chip starts
from scratch every power cycle and takes its usual time to settle.

| field | default | |
| --- | --- | --- |
| `link` | required | an `i2c` link |
| `address` | `0x58` | fixed |

### `sgp40`

Sensirion SGP40: `voc_raw` (dimensionless), `[RP]`. Raw signal only -- no
VOC-index algorithm. The chip takes a humidity/temperature compensation
input per read; the driver class can be given the addresses of another
sensor's readings for that, but the rig file cannot set them yet, so every
read uses the datasheet defaults (50 %RH, 25 °C).

| field | default | |
| --- | --- | --- |
| `link` | required | an `i2c` link |
| `address` | `0x59` | fixed |

### `ccs811`

ams/ScioSense CCS811: `co2eq` (ppm), `tvoc` (ppb), both `[RP]`. Runs a
mandatory boot/app-start sequence before its first read.

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
engineering units. Below ~3.6 mA or above ~21 mA is treated as a wiring
fault, not a real reading (the NAMUR NE43 convention).

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
pump on the way out, including on an error mid-dispense. Stepper-driven
pumps (step/direction) aren't supported directly here -- pair a `stepper`
with your own dispense logic instead.

| field | default | |
| --- | --- | --- |
| `pump` | required | a `pwm_channel` or `gpio_line` config |
| `ml_per_s` | required | the pump's rate at full drive (PWM) or while on (relay) |
| `max_dispense_ml` | none | an optional per-call cap |
| `drive_fraction` | `1.0` | the duty to run a `pwm_channel` pump at during a dispense; refused on a `gpio_line` pump unless left at `1.0` |

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

### `stepper`

A step/direction stepper motor -- the interface almost every real driver
IC exposes (A4988, DRV8825, TMC-series), not raw phase bit-banging, which
would be exactly the kind of fragile Python timing `hx711` is already
flagged for. A motorized valve, damper, vent or linear actuator. A
`move(steps)` command (not `move_to(position)` -- no homing/limit-switch
story to trust an absolute target against) clocks out a pulse train at
`steps_per_s`, always leaving the driver safe (direction settled,
`enable_line` released) even on an error mid-move.

| field | default | |
| --- | --- | --- |
| `link` | required | a `gpio` link |
| `step_line`, `direction_line` | required | |
| `steps_per_s` | required | |
| `enable_line` | none | driven active for the move's duration, released after |
| `enable_active_low` | `true` | the common driver-IC convention |
| `steps_per_unit` | none | lets `move()` take engineering units (degrees, mm) instead of raw steps |
| `pulse_width_s` | `0.0005` | how long the step line is held high per pulse |

`position` -- the raw step count -- is `[R]` only (readable on demand,
never published/recorded by default): internal plumbing for the move
command, not a quantity a rig cares to trend. A rig-file `signals:`
override cannot widen this to `[RP]` unless the driver names it a
[ceiling](../../7-reference/rig-file.md) -- `stepper` doesn't, on
purpose, so upgrading it needs a driver code change, not a config
change.

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
