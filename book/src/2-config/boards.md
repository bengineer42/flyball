# Boards and Linux I/O

A single-board computer is not special. I²C is `/dev/i2c-N`, SPI is
`/dev/spidevN.M`, GPIO is `/dev/gpiochipN`, hardware PWM and 1-Wire are
sysfs. A Raspberry Pi, a BeagleBone, a Jetson, or a laptop with an FT232H
bridge all present the same files with different numbers. So there is one
package, `flyball-linux`, for the buses, and the numbers live in a **board
profile**: a data file, not code.

```
pip install flyball-linux[i2c,gpio]      # only the buses you use
flyball-linux probe                      # what this machine has
```

## Links and devices

`flyball-linux` registers its tags through the `flyball.configs` entry
point, so `flyball rig check`, `flyball rig schema` and the runner know them
once it is installed.

| link type | device | fake |
| --- | --- | --- |
| `i2c` | `/dev/i2c-<bus>` via smbus2 | `fake_i2c` — registers per address, scripted raw replies |
| `spi` | `/dev/spidev<bus>.<device>` via spidev | `fake_spi` — scripted or computed replies |
| `gpio` | `/dev/<chip>` via libgpiod v2 | `fake_gpio` — levels per line |
| `pwm` | `/sys/class/pwm/pwmchip<chip>` | `fake_pwm` — period and duty per channel |
| `onewire` | `/sys/bus/w1/devices` | `fake_onewire` — `w1_slave` text per device |
| `uart` | a kernel serial device via pyserial: `port` (required, e.g. `/dev/ttyUSB0`), `baudrate` (`9600`), `timeout` (`1.0` s per read) | `fake_uart` — scripted `replies` |

| device driver | what | on |
| --- | --- | --- |
| `i2c_table` | a table of registers: `address`, `length`, `signed`, `byteorder`, `shift`, `scale`, `offset`, `unit`, `write` | `i2c` |
| `sht4x` | Sensirion SHT40/41/45: one chip, `humidity`, `temperature [RP]` | `i2c` |
| `sht4x_set` | several SHT4x chips on one bus, each its own atomic namespace | `i2c` |
| `ads1115` | TI 16-bit ADC, four single-ended channels, PGA gain | `i2c` |
| `mcp3008` | Microchip 10-bit ADC, eight channels | `spi` |
| `gpio_line` | `direction: output` (default): one `[W]` signal `on`, plus `on`/`off` commands (refused while a controller drives `on`); `direction: input`: one `[RP]` signal `level` | `gpio` |
| `pwm_channel` | one `[W]` signal `drive`: the duty itself (0-1), or a unit and `span` mapping it linearly (a feedforward) | `pwm` |
| `ds18b20` | the `w1_therm` family, in °C | `onewire` |

`i2c_table` covers most register-mapped sensors (TMP117, MCP9808, INA219,
LM75) without a driver, and is its own config's tree — the same pattern as
`scpi`'s `channels:`:

```yaml
board_temp:
  driver: i2c_table
  link: i2c1
  address: 0x48
  registers:
    temperature: { address: 0, length: 2, signed: true, scale: 0.0078125, unit: "°C" }
```

A chip with a command sequence rather than registers (SHT4x: write a byte,
wait, read six) gets its own driver under `flyball_chips` (extensions/chips). Each
is a short module against the link protocol, tested to the byte on the fake.

## Board profiles

A profile declares a machine's links and names its pins.
`extensions/linux/src/flyball_linux/boards/rpi5.toml`, quoted in part as the
file actually is:

```toml
name = "Raspberry Pi 5"

[links.i2c1]
type = "i2c"
bus = 1

[links.header]
type = "gpio"
chip = "gpiochip4"

[links.pwm]
type = "pwm"
chip = 0

[pins]
GPIO18 = { link = "header", line = 18 }
PWM0   = { link = "pwm", channel = 0 }
```

A rig file names it and then refers to pins by label. `extensions/linux/examples/greenhouse.yaml`,
quoted in part:

```yaml
name: greenhouse
board: rpi5

devices:
  heater:
    driver: pwm_channel
    label: Heater
    pin: PWM0                        # the board's `pwm` link, channel 0
    frequency_hz: 1000
    unit: "°C"                       # `drive` is a temperature: off holds 10 °C, flat out 40;
    quantity: temperature            # the controller corrects the rest
    span: [10, 40]
  fan:
    driver: gpio_line
    label: Fan
    pin: GPIO18                      # becomes link: header, line: 18 -- `flyball invoke fan on`, `flyball invoke fan off`

controllers:
  heater.drive:
    measured: air.temperature
    law: { type: PI, kp: 0.5, ki: 0.01 }
    default: true
```

The profile's links go underneath the file's own (the file wins on a
clash), and `pin: "LABEL"` becomes the fields the profile gives that label,
again with the entry's own fields winning. `flyball rig check` says which
profile file it used.

Profiles are looked up in `$FLYBALL_BOARDS`, then each installed package's
own profiles (registered under the `flyball.board_dirs` entry point), then a
`boards/` directory beside the rig file or in any directory above it for
profiles of your own, then `~/.config/flyball/boards` and
`/etc/flyball/boards`. `board: ./mine.toml` is a path relative to the rig
file. `flyball-linux` registers [`extensions/linux/src/flyball_linux/boards/`](https://github.com/bengineer42/flyball/tree/main/extensions/linux/src/flyball_linux/boards)
this way: `rpi4`, `rpi5`, `beaglebone_black`, `generic` and `sim`; none is
loaded until a rig file asks for it, and adding a board is adding a file.

`sim` is every link as a fake. A rig file written for a real board runs on
any machine with an overlay that sets `board: sim`, its pin labels resolving
to fake chips — `extensions/linux/examples/sim.yaml` does exactly this over
`greenhouse.yaml`, scripting the I²C and 1-Wire fakes to answer fixed
readings, which is how the example is tested:

```
cd extensions/linux/examples
flyball rig check greenhouse.yaml sim.yaml
flyball-runner greenhouse.yaml sim.yaml
```

## What is board-specific

Only values: which `gpiochip` the header is (the Pi 5 moved it), which PWM
channel a pin has, which overlay lines enable a bus. Those are comments in
the profile and the board's own documentation, not code. Anything that
needs microsecond timing — software PWM, DHT22's bit-banged protocol — is
deliberately absent: it does not work on a non-realtime kernel and belongs
on a microcontroller behind a `serial` link.
