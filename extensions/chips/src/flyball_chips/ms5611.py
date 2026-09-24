"""TE Connectivity MS5611-01BA03: barometric pressure and temperature over I2C.

Decoded from the public MS5611-01BA03 datasheet; this driver has never been
run against real hardware.

PROM-based factory calibration: six 16-bit coefficients (`C1`..`C6`), each
behind its own PROM-read command, plus a CRC nibble in word 7 that this
driver does not check (the datasheet's CRC-4 is over the whole 8-word PROM
image, not a single coefficient, and is only meaningful at power-up over the
raw words -- decoding it correctly needs the manufacturer's own reference
code, which is `[Unverified]` here, so it is left unchecked rather than
implemented against a guess). A conversion is triggered separately for
pressure (`D1`) and temperature (`D2`) at one of five OSR settings, each
needing the datasheet's own worst-case conversion delay before the 24-bit
ADC result is read back. Compensation combines both raw readings with the
six coefficients in the second-order form the datasheet gives (including
the low-temperature correction below 20°C).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Literal

from flyball.foundation.config import resolve
from flyball.foundation.device import DriverConfig, Node, Readable, Readout, Sample
from flyball.foundation.errors import HardwareError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Pascal
from flyball.hardware.i2c import I2cLink
from pydantic import Field

from flyball_chips._links import I2cLinkConfig

MS5611_ADDRESS = 0x77
"""CSB pin low; CSB high answers at 0x76."""

RESET = 0x1E  # TE MS5611-01BA03 datasheet, command table: Reset 0x1E
_PROM_BASE = 0xA0  # PROM read: 0xA0 + 2*index, index 0..7
_D1_BASE = 0x40  # convert D1 (pressure)
_D2_BASE = 0x50  # convert D2 (temperature)
_ADC_READ = 0x00

Osr = Literal[256, 512, 1024, 2048, 4096]
OSR_COMMANDS: dict[Osr, tuple[int, float]] = {
    256: (0x00, 0.0006),
    512: (0x02, 0.0012),
    1024: (0x04, 0.0023),
    2048: (0x06, 0.0043),
    4096: (0x08, 0.0091),
}
"""OSR command offset (added to the D1/D2 base) and the datasheet's max conversion time."""

PRESSURE = Quantity("pressure", Pascal)
TEMPERATURE = Quantity("temperature", Celsius)


def prom_command(word: int) -> int:
    """The PROM-read command for `word` (0..7): word 0 is the manufacturer/reserved word."""
    if not 0 <= word <= 7:
        raise ValueError(f"PROM word must be 0..7, got {word}")
    return _PROM_BASE + 2 * word


def compensate(
    d1: int, d2: int, coefficients: tuple[int, int, int, int, int, int]
) -> tuple[float, float]:
    """(pressure Pa, temperature °C) from raw D1/D2 and PROM words C1..C6.

    The datasheet's second-order compensation, including the low-temperature
    (`dT < 20°C`) correction; the very-low-temperature (`< -15°C`) term is
    not applied (this rig does not operate there).
    """
    c1, c2, c3, c4, c5, c6 = coefficients
    dt = d2 - c5 * 256
    temp = 2000 + dt * c6 / 8388608  # 1/100 °C, i.e. centi-degrees

    off = c2 * 65536 + (c3 * dt) / 128
    sens = c1 * 32768 + (c4 * dt) / 256

    if temp < 2000:
        t2 = dt * dt / 2147483648
        low = (temp - 2000) * (temp - 2000)
        off2 = 5 * low / 2
        sens2 = 5 * low / 4
        temp -= t2
        off -= off2
        sens -= sens2

    pressure_raw = (d1 * sens / 2097152 - off) / 32768  # centi-Pa (0.01 mbar = 1 Pa)
    return pressure_raw, temp / 100.0


class Ms5611Sensor:
    """One chip at `i2c_address`: PROM read once at init, then D1/D2 conversions per `read`."""

    __slots__ = ("i2c_address", "coefficients", "link", "osr", "sleep")

    def __init__(self, link: I2cLink, i2c_address: int, osr: Osr = 4096, sleep: bool = True) -> None:
        self.link = link
        self.i2c_address = i2c_address
        self.osr: Osr = osr
        self.sleep = sleep
        """Whether to wait the conversion time; off in a test against a fake."""
        self.link.write(i2c_address, [RESET])
        if self.sleep:
            time.sleep(0.003)  # datasheet: 2.8 ms reload
        self.coefficients = self._read_prom()

    def _read_prom(self) -> tuple[int, int, int, int, int, int]:
        words = []
        for word in range(1, 7):
            self.link.write(self.i2c_address, [prom_command(word)])
            raw = self.link.read(self.i2c_address, 2)
            words.append(int.from_bytes(raw, "big"))
        return tuple(words)  # type: ignore[return-value]

    def _convert(self, base: int) -> int:
        offset, wait_s = OSR_COMMANDS[self.osr]
        self.link.write(self.i2c_address, [base + offset])
        if self.sleep:
            time.sleep(wait_s)
        self.link.write(self.i2c_address, [_ADC_READ])
        raw = self.link.read(self.i2c_address, 3)
        if len(raw) != 3:
            raise HardwareError(f"MS5611 ADC reply is {len(raw)} bytes, not 3")
        return int.from_bytes(raw, "big")

    def read(self) -> tuple[float, float]:
        """(pressure Pa, temperature °C): a D1 conversion, a D2 conversion, then compensation."""
        d1 = self._convert(_D1_BASE)
        d2 = self._convert(_D2_BASE)
        return compensate(d1, d2, self.coefficients)


class Ms5611(Readable):
    """One chip on the device root: `pressure`, `temperature [RP]`."""

    pressure = Readout("pressure", quantity=PRESSURE, range=(10000.0, 120000.0), precision=1)
    temperature = Readout("temperature", quantity=TEMPERATURE, range=(-40.0, 85.0), precision=2)

    def __init__(
        self,
        name: str,
        link: I2cLink,
        i2c_address: int = MS5611_ADDRESS,
        osr: Osr = 4096,
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.sensor = Ms5611Sensor(link, i2c_address, osr, sleep)

    @property
    def config(self) -> Ms5611Config:
        return Ms5611Config(link="", i2c_address=self.sensor.i2c_address, osr=self.sensor.osr)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        pressure, temperature = self.sensor.read()
        yield self.sample(time_ns, pressure=pressure, temperature=temperature)


class Ms5611Config(DriverConfig[Ms5611], type="ms5611"):
    """One chip by its I2C address; `osr` trades conversion time for resolution."""

    link: I2cLinkConfig | str  # type: ignore[valid-type]
    i2c_address: int = Field(default=MS5611_ADDRESS, ge=0x03, le=0x77)
    osr: Osr = 4096

    def build(self, name: str, label: str | None = None) -> Ms5611:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Ms5611(name, resolve(self.link), self.i2c_address, self.osr, label=label)


Ms5611.config_type = Ms5611Config  # the config is declared after the device it builds


__all__ = [
    "MS5611_ADDRESS",
    "OSR_COMMANDS",
    "PRESSURE",
    "TEMPERATURE",
    "Ms5611",
    "Ms5611Config",
    "Ms5611Sensor",
    "Osr",
    "compensate",
    "prom_command",
]
