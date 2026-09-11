from __future__ import annotations

from collections.abc import Generator

from linux_pwm import PWMChannel, PWMChip

from humctrl.core import Normalised
from humctrl.core.errors import UnachievableError
from humctrl.core.reading import Channel
from humctrl.i2c import I2CBus
from humctrl.pumps.drivers import PumpDriver
from humctrl.pumps.errors import PumpError
from humctrl.readers import HTReaderSource, HTReading, HTReadings, HTSetReader
from humctrl.sht4x import _MUX_ADDR, _SHT4X_ADDR, MuxedSHT4xBank, SHT4x

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


def labelled[T](dry: T, wet: T, process: T) -> Generator[tuple[HTReaderSource, T], None, None]:
    yield HTReaderSource.DRY, dry
    yield HTReaderSource.WET, wet
    yield HTReaderSource.PROCESS, process


class MuxedI2CSHT4xReaders(HTSetReader):
    bank: MuxedSHT4xBank
    _channels: set[Channel]

    def __init__(
        self,
        i2c: I2CBus,
        process_port: int | None = None,
        dry_port: int | None = None,
        wet_port: int | None = None,
        mux_address: int = _MUX_ADDR,
        sensor_address: int = _SHT4X_ADDR,
    ) -> None:
        channels = {
            label: channel
            for label, channel in labelled(dry_port, wet_port, process_port)
            if channel is not None
        }
        self._channels = {Channel(s, q, q.unit) for s in channels for q in HTQuantity}
        self.bank = MuxedSHT4xBank(i2c, channels, mux_address, sensor_address)

    @property
    def channels(self) -> set[Channel]:
        return self._channels

    def read_process(self) -> HTReading | Exception | None:
        return self.bank.read(self.clock.now_ns(), "process")

    def read_dry(self) -> HTReading | Exception | None:
        return self.bank.read(self.clock.now_ns(), "dry")

    def read_wet(self) -> HTReading | Exception | None:
        return self.bank.read(self.clock.now_ns(), "wet")

    def read_all(self) -> HTReadings:
        readings = self.bank.read(self.clock.now_ns(), ("dry", "wet", "process"))
        return HTReadings(**readings)

    def read(self, process: bool = True, wet: bool = True, dry: bool = True) -> HTReadings:
        sensors = [label for label, flag in labelled(dry, wet, process) if flag]
        readings = self.bank.read(self.clock.now_ns(), sensors)
        return HTReadings(**readings)


class I2CSHT4xReader(HTSetReader):
    source: HTReaderSource
    sensor: SHT4x

    def _read(self) -> HTReading | Exception | None:
        return self.sensor.read(self.clock.now_ns())

    def __init__(
        self,
        i2c: I2CBus,
        source: HTReaderSource = HTReaderSource.PROCESS,
        address: int = _SHT4X_ADDR,
    ) -> None:
        self._channels = {Channel(source, q, q.unit) for q in HTQuantity}
        self.source = source
        self.sensor = SHT4x(i2c, source.value, address)
        assert hasattr(self, f"read_{source.value}"), (
            f"Source {source.value} is not a valid reader source"
        )
        setattr(self, f"read_{source.value}", self._read)

    @property
    def channels(self) -> set[Channel]:
        return self._channels
