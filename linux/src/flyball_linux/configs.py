"""The entry-point target: importing this registers every tag the package defines."""

from flyball_linux.devices import (  # ruff: ignore[unused-import]
    current_loop,
    dosing_pump,
    gpio,
    i2c_table,
    onewire,
    pulse_counter,
    pwm,
)
from flyball_linux.devices.chips import (  # ruff: ignore[unused-import]
    ads1115,
    bme280,
    bme680,
    ccs811,
    ezo_ph,
    htu21d,
    hx711,
    mcp3008,
    mhz19,
    ms5611,
    scd4x,
    scd30,
    sgp30,
    sgp40,
    sht4x,
    sht31,
)
from flyball_linux.links import gpio as gpio_link  # ruff: ignore[unused-import]
from flyball_linux.links import (  # ruff: ignore[unused-import]
    i2c,
    spi,
    uart,
)
from flyball_linux.links import onewire as onewire_link  # ruff: ignore[unused-import]
from flyball_linux.links import pwm as pwm_link  # ruff: ignore[unused-import]
