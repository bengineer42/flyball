import board
from adafruit_sht4x import _SHT4X_DEFAULT_ADDR, SHT4x
from busio import I2C
from linux_pwm import PWMChannel, PWMChip

from humctrl.clock import Clock
from humctrl.error import UnachievableError
from humctrl.pumps import DualPumps, DualPumpsConfig
from humctrl.pumps.drivers import PumpDriver, PumpPair
from humctrl.pumps.errors import PumpError
from humctrl.pumps.types import MaxFlows
from humctrl.readers import Reader, Reading
from humctrl.typing import Normalised

DEFAULT_PWM_FREQUENCY: float = 20_000.0  # Hz


class PumpFlowError(PumpError, UnachievableError):
    """A requested flow or fraction cannot be applied."""

    def __init__(self, flow: float, max_flow: float, name: str | None = None) -> None:
        self.flow = flow
        self.max_flow = max_flow
        self.name = name
        super().__init__(
            f"flow {flow} exceeds max_flow {max_flow}" + (f" for {name}" if name else "")
        )


class LinuxPwmDualPumpsConfig(DualPumpsConfig):
    wet_channel: int = 0
    dry_channel: int = 1
    pwm_chip: int = 0
    pwm_frequency: float = DEFAULT_PWM_FREQUENCY
    wet_deadband: Normalised = 0.0
    dry_deadband: Normalised = 0.0
    max_flows: MaxFlows = MaxFlows(1.0, 1.0)
    flow_units: str | None = None

    def build(self) -> DualPumps:
        return DualPumps(self.build_driver(), self.max_flows, units=self.flow_units)

    def build_driver(self) -> PumpPair:
        return setup_linux_pwm_pumps_interface(
            wet_deadband=self.wet_deadband,
            dry_deadband=self.dry_deadband,
            frequency=self.pwm_frequency,
            wet_channel=self.wet_channel,
            dry_channel=self.dry_channel,
            chip=self.pwm_chip,
        )


class LinuxPWMPump(PumpDriver):
    name: str
    pwm: PWMChannel
    _frequency: float
    _deadband: float
    _effort: float = 0.0

    def __init__(
        self,
        channel: int,
        frequency: float,
        deadband: float = 0.0,
        chip: PWMChip | int = 0,
        timeout: float = 10,
    ) -> None:
        self._frequency = frequency
        self._deadband = deadband
        self.pwm = PWMChannel(channel=channel, chip=chip, timeout=timeout)
        self.pwm.set_frequency(frequency)

    @property
    def deadband(self) -> float:
        return self._deadband

    @property
    def effort(self) -> float:
        return self._effort

    def calculate_duty_ratio(self, effort: float) -> float:
        return (1.0 - self._deadband) * max(0.0, min(effort, 1.0)) + self._deadband

    def set_effort(self, effort: float) -> Normalised:
        self.pwm.set_duty_ratio(self.calculate_duty_ratio(effort))
        if effort > 0.0 and not self.pwm.enabled:
            self.pwm.enable()
        self._effort = effort
        return self._effort

    def stop(self) -> None:
        self.pwm.stop()


def setup_linux_pwm_pumps_interface(
    wet_deadband: float = 0,
    dry_deadband: float = 0,
    frequency: float = 20_000,
    wet_channel: int = 0,
    dry_channel: int = 1,
    chip: int | PWMChip = 0,
    timeout: float = 10,
) -> PumpPair:
    chip = chip if isinstance(chip, PWMChip) else PWMChip(chip)
    wet = LinuxPWMPump(wet_channel, frequency, wet_deadband, chip, timeout=timeout)
    dry = LinuxPWMPump(dry_channel, frequency, dry_deadband, chip, timeout=timeout)
    return PumpPair(wet, dry)


class I2CSHT4x(Reader):
    _sensor: SHT4x
    _clock: Clock

    def __init__(
        self,
        label: str,
        i2c: I2C | None = None,
        address: int = _SHT4X_DEFAULT_ADDR,
        clock: Clock | None = None,
    ) -> None:
        self._label = label
        self.i2c = board.I2C() if i2c is None else i2c
        self._sensor = SHT4x(self.i2c, address)
        self._clock = Clock() if clock is None else clock

    def read(self) -> Reading:
        temperature, humidity = self._sensor.measurements
        return Reading(self._clock.now_ns(), humidity, temperature, self._label)

    def clock(self) -> Clock:
        return self._clock
