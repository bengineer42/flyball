"""The entry-point target: importing this registers every tag the package defines."""

from flyball_linux.devices import (  # ruff: ignore[unused-import]
    current_loop,
    dosing_pump,
    gpio,
    i2c_table,
    onewire,
    pulse_counter,
    pwm,
    stepper,
)
from flyball_linux.links import gpio as gpio_link  # ruff: ignore[unused-import]
from flyball_linux.links import (  # ruff: ignore[unused-import]
    i2c,
    spi,
    uart,
)
from flyball_linux.links import onewire as onewire_link  # ruff: ignore[unused-import]
from flyball_linux.links import pwm as pwm_link  # ruff: ignore[unused-import]
