# flyball-linux

Linux I/O for flyball rigs: I²C, SPI, GPIO, PWM and 1-Wire links, and the
devices on them. Nothing here is board-specific; which bus numbers a board
has is a profile in [../boards/](../boards/).

```sh
uv sync --all-extras          # dev environment; the extras are the bus libraries
make check                    # ruff, pyright, pytest -- all on fake buses
uv run flyball rig check examples/greenhouse.yaml examples/sim.yaml
uv run flyball-linux probe    # what this machine has, as rig-file fragments
```

The package registers its tags through the `flyball.configs` entry point,
so `flyball rig check`, `flyball rig schema` and the daemon know them once
it is installed.

## Links

| tag | what | fake |
| --- | --- | --- |
| `i2c` | `/dev/i2c-<bus>` via smbus2 | `fake_i2c` -- registers per address, scripted raw replies |
| `spi` | `/dev/spidev<bus>.<device>` via spidev | `fake_spi` -- scripted or computed replies |
| `gpio` | `/dev/<chip>` via libgpiod v2 | `fake_gpio` -- levels per line |
| `pwm` | `/sys/class/pwm/pwmchip<chip>` | `fake_pwm` -- period and duty per channel |
| `onewire` | `/sys/bus/w1/devices` | `fake_onewire` -- `w1_slave` text per device |

## Devices

Every driver is a `Device` with a tree of signals, each `[R]`eadable,
`[P]`ublishing or `[W]`ritable (`temp-docs/DEVICE-MODEL-PLAN.md`). Its own
settings sit flat beside the envelope (`driver`, `label`, `poll_s`,
`signals`, `bound`) or under `config`; the envelope's `signals:` overrides
range, precision, bands, limits and `poll_s` per signal.

| driver | config | signals | on |
| --- | --- | --- | --- |
| `sht4x` | `link`, `address` (0x44), `precision` | `humidity [RP] %RH`, `temperature [RP] °C` | `i2c` |
| `sht4x_set` | `link`, `sensors: {name: {address}}`, `precision` | one atomic namespace per sensor: `name.humidity`, `name.temperature [RP]`, each on its own `poll_s` | `i2c` |
| `ads1115` | `link`, `address`, `gain`, `channels: {name: {channel, unit, quantity?, scale?, offset?}}` | one `[RP]` per channel, `volts * scale + offset` | `i2c` |
| `mcp3008` | `link`, `vref`, `channels: {name: {channel, unit, quantity?, scale?, offset?}}` | one `[RP]` per channel | `spi` |
| `i2c_table` | `link`, `address`, `registers: {name: {address, length, signed, byteorder, shift, scale, offset, unit, write?}}` | one `[RP]` per register; `write: true` makes it `[RPW]` and a demand writes it | `i2c` |
| `gpio_line` | `link`, `line`, `direction` (output), `invert`, `initial`, `pull_up` | an output: `on [W]` 0/1, commands `on`/`off`; an input: `level [RP]` 0/1 | `gpio` |
| `pwm_channel` | `link`, `channel`, `frequency_hz`, `invert`, `unit?`, `quantity?`, `span?` | `drive [W]`: the duty 0..1, or in `unit` mapped linearly over `span`; commands `set_frequency`, `off` | `pwm` |
| `ds18b20` | `link`, `device` | `temperature [RP] °C` | `onewire` |

The table driver covers most register-mapped sensors (TMP117, MCP9808,
INA219, LM75) and single-register DACs without a driver of their own:

```yaml
devices:
  board_temp:
    driver: i2c_table
    poll_s: 1
    link: i2c1
    address: 0x48
    registers:
      temperature: { address: 0, length: 2, signed: true, scale: 0.0078125, unit: "°C" }
  dac:
    driver: i2c_table
    link: i2c1
    address: 0x60
    registers:
      out: { address: 0x40, length: 2, scale: 0.001, unit: V, write: true }   # dac.out [RPW]
```

A chip with a command sequence rather than registers (SHT4x: write a byte,
wait, read six) gets its own driver under `flyball_linux.devices.chips`.
Each is a short module against the link protocol, tested to the byte on the
fake.

## The example: a greenhouse

[examples/greenhouse.yaml](examples/greenhouse.yaml) is a Pi 5 with an
SHT4x in the air, a DS18B20 in the soil, a PWM heater and a relay fan;
`board: rpi5` supplies the links and a device names its pin:

```yaml
name: greenhouse
board: rpi5
devices:
  air:    { driver: sht4x,   label: Air,  poll_s: 2, link: i2c1 }
  soil:   { driver: ds18b20, label: Soil, poll_s: 5, link: w1, device: 28-0316a279e7ff }
  heater:                              # heater.drive [W] in °C: off holds 10, flat out 40
    driver: pwm_channel
    pin: PWM0                          # the board's `pwm` link, channel 0
    unit: "°C"
    quantity: temperature
    span: [10, 40]
  fan:    { driver: gpio_line, label: Fan, pin: GPIO18 }     # fan.on [W]; `flyball fan on`
controllers:
  heater.drive: { signal: air.temperature, law: { tag: PI, kp: 0.5, ki: 0.01 }, default: true }
```

[examples/sim.yaml](examples/sim.yaml) overlays it with `board: sim`, every
link a fake and the I²C and 1-Wire buses scripted, so the same drivers,
names and addresses run on any machine:

```sh
uv run flyball rig check examples/greenhouse.yaml examples/sim.yaml
uv run flyball-daemon examples/greenhouse.yaml examples/sim.yaml
```

Documentation: the book's "Boards and Linux I/O" page.
