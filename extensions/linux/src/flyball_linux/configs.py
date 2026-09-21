"""The entry-point target: explicitly registers every tag this package provides."""

from flyball.model.catalog import Catalogs

from flyball_linux.devices.current_loop import CurrentLoopConfig
from flyball_linux.devices.dosing_pump import DosingPumpConfig
from flyball_linux.devices.gpio import GpioLineConfig
from flyball_linux.devices.i2c_table import I2cTableConfig
from flyball_linux.devices.onewire import Ds18b20Config
from flyball_linux.devices.pulse_counter import PulseCounterConfig
from flyball_linux.devices.pwm import PwmChannelConfig
from flyball_linux.devices.stepper import StepperConfig
from flyball_linux.links.gpio import GpioConfig
from flyball_linux.links.i2c import I2cConfig
from flyball_linux.links.onewire import FakeOneWireConfig, OneWireConfig
from flyball_linux.links.pwm import FakePwmConfig, PwmConfig
from flyball_linux.links.spi import SpiConfig
from flyball_linux.links.uart import SerialConfig


def register(catalog: Catalogs) -> None:
    catalog.register_device(DosingPumpConfig)
    catalog.register_device(GpioLineConfig)
    catalog.register_device(Ds18b20Config)
    catalog.register_device(CurrentLoopConfig)
    catalog.register_device(I2cTableConfig)
    catalog.register_device(PwmChannelConfig)
    catalog.register_device(PulseCounterConfig)
    catalog.register_device(StepperConfig)
    catalog.register_link(I2cConfig)
    catalog.register_link(FakeOneWireConfig)
    catalog.register_link(OneWireConfig)
    catalog.register_link(GpioConfig)
    catalog.register_link(FakePwmConfig)
    catalog.register_link(PwmConfig)
    catalog.register_link(SerialConfig)
    catalog.register_link(SpiConfig)
