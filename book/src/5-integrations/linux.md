# Raspberry Pi and Linux buses

**What.** `flyball-linux` (`extensions/linux/` in the repository): the kernel's I²C, SPI,
GPIO, PWM and 1-Wire interfaces as links, and drivers for the chips that
commonly sit on them -- SHT4x humidity sensors, ADS1115 and MCP3008 ADCs,
DS18B20 thermometers, a GPIO line as a relay or switch, a PWM channel as an
actuator, and a register table for any I²C chip without a driver of its own.

**What comes through.** A signal per reading or output, in the chip's real
unit; a fake per link, so a Pi rig runs on a laptop byte for byte.

**Configure.** A board profile names the buses and pin labels once
([Boards and Linux I/O](../2-config/boards.md)); devices then say `pin: LABEL`.
Every driver's fields: [Supported drivers → Board chips](../2-config/devices/drivers.md#board-chips-flyball-linux).

**Code.** `flyball_linux.links.*`, `flyball_linux.devices.*`, registered
through the `flyball.configs` entry point on install.

**A complete rig on it.** [The humidity rig](https://bengineer42.github.io/flyball/humidity/) -- its [board and wiring](https://bengineer42.github.io/flyball/humidity/4-hardware/board/) and [Pi setup](https://bengineer42.github.io/flyball/humidity/4-hardware/pi/).
