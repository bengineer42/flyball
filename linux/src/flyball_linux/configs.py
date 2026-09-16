"""The entry-point target: importing this registers every tag the package defines."""

from flyball_linux.devices import gpio, i2c_table, onewire, pwm  # ruff: ignore[unused-import]
from flyball_linux.devices.chips import ads1115, mcp3008, sht4x  # ruff: ignore[unused-import]
from flyball_linux.links import gpio as gpio_link  # ruff: ignore[unused-import]
from flyball_linux.links import i2c, spi  # ruff: ignore[unused-import]
from flyball_linux.links import onewire as onewire_link  # ruff: ignore[unused-import]
from flyball_linux.links import pwm as pwm_link  # ruff: ignore[unused-import]
