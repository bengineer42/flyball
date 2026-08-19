import time

import board
from adafruit_sht4x import _SHT4X_DEFAULT_ADDR, SHT4x
from adafruit_sht4x import Mode as SHT4XMode
from busio import I2C
from linux_pwm import PWMChannel, PWMChip

from humctrl.pumps import DualPumps, Pump
from humctrl.sensors import HumidityTemperatureReading, HumidityTemperatureSensor
from humctrl.utils import validate_normalised


class PumpCalibration:
    max_flow: float
    deadband: float

    def __init__(self, max_flow: float, deadband: float):
        validate_normalised("deadband", deadband)
        if max_flow <= 0.0:
            raise ValueError("max_flow must be positive")
        self.max_flow = max_flow
        self.deadband = deadband

    def calculate_duty_ratio(self, flow: float) -> float:
        if flow <= 0.0:
            return 0.0
        return (1.0 - self.deadband) * (flow / self.max_flow) + self.deadband


class LinuxPWMPump(Pump):
    pwm: PWMChannel
    _frequency: float
    calibration: PumpCalibration

    def __init__(
        self,
        channel: int,
        frequency: float,
        max_flow: float = 1.0,
        deadband: float = 0.0,
        chip: PWMChip | int = 0,
        timeout: float = 10,
    ):
        self._frequency = frequency
        self.calibration = PumpCalibration(max_flow, deadband)
        self.pwm = PWMChannel(channel=channel, chip=chip, timeout=timeout)
        self.pwm.set_frequency(frequency)

    def set_flow(self, flow: float):
        self.pwm.set_duty_ratio(self.calibration.calculate_duty_ratio(flow))
        if flow > 0.0 and not self.pwm.enabled:
            self.pwm.enable()

    def stop(self):
        self.pwm.stop()


class LinuxPWMPumps(DualPumps):
    def __init__(
        self,
        wet_max_flow: float,
        wet_deadband: float,
        dry_max_flow: float,
        dry_deadband: float,
        frequency: float = 20_000,
        wet_channel: int = 0,
        dry_channel: int = 1,
        chip: int = 0,
        timeout: float = 10,
    ):
        chip: PWMChip = PWMChip(chip)
        self.wet_pump = LinuxPWMPump(
            wet_channel,
            frequency,
            wet_max_flow,
            wet_deadband,
            chip,
            timeout=timeout,
        )
        self.dry_pump = LinuxPWMPump(
            dry_channel,
            frequency,
            dry_max_flow,
            dry_deadband,
            chip,
            timeout=timeout,
        )


class I2CSHT4x(HumidityTemperatureSensor):
    sensor: SHT4x
    monatomic_offset: float = 0

    def __init__(
        self,
        i2c: I2C | None = None,
        address: int = _SHT4X_DEFAULT_ADDR,
        monatomic_offset=0,
    ):
        self.i2c = board.I2C() if i2c is None else i2c
        self.sensor = SHT4x(self.i2c, address)
        self.sensor.mode = SHT4XMode.NOHEAT_HIGHPRECISION
        self.monatomic_offset = monatomic_offset

    def read(self) -> HumidityTemperatureReading:
        HumidityTemperatureReading(
            time.monotonic() + self.monatomic_offset, *self.sensor.measurements
        )
