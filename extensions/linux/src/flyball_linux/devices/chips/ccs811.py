"""ams/ScioSense CCS811: eCO2/TVOC over I2C, register-based, not command/CRC like Sensirion's chips.

Boots into a bootloader; a `write` of `APP_START` with no data is mandatory
to leave boot mode before `ALG_RESULT_DATA` answers anything, so `Ccs811`
runs that sequence itself in `__init__` (raising if the chip never reports
app mode) rather than leaving it to the caller. Decoded from ams's public
datasheet and programming guide; this driver has never been run against a
real chip.

[Unverified] The bit-packing of `ENV_DATA` (humidity as a 1/512 %RH fixed
point value, temperature the same but biased by +25 degC, each as two
bytes big-endian) follows the convention used by several open CCS811
libraries rather than a directly re-read datasheet table; treat
`encode_env_data` as unconfirmed.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from flyball.core.config import resolve
from flyball.core.device import DriverConfig, Output, Readable
from flyball.core.errors import HardwareError
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, Node, Sample
from flyball.core.units.si import PartsPerBillion, PartsPerMillion
from pydantic import Field

from flyball_linux.links.i2c import I2cLink, I2cLinkConfig

CO2EQ = Quantity("CO2 equivalent", PartsPerMillion)
TVOC = Quantity("total VOC", PartsPerBillion)
CCS811_ADDRESS = 0x5A
"""The default ADDR-low address; ADDR pulled high answers at 0x5B."""

STATUS = 0x00
MEAS_MODE = 0x01
ALG_RESULT_DATA = 0x02
RAW_DATA = 0x03
ENV_DATA = 0x05
THRESHOLDS = 0x10
BASELINE = 0x11
HW_ID = 0x20
HW_VERSION = 0x21
ERROR_ID = 0xE0
APP_START = 0xF4
SW_RESET = 0xFF

HW_ID_VALUE = 0x81
"""The fixed value `HW_ID` reads back, for a sanity check nothing else answered."""

STATUS_FW_MODE = 0x80
"""Set once the chip has left the bootloader and is running its application firmware."""
STATUS_DATA_READY = 0x08
STATUS_ERROR = 0x01

DRIVE_MODE_1S = 0x10
"""`MEAS_MODE`: constant power, one measurement per second (the common default)."""


def encode_env_data(humidity_percent_rh: float, temperature_c: float) -> list[int]:
    """`ENV_DATA`'s four bytes: humidity then temperature, each 1/512 %RH or degC resolution.

    [Unverified] see the module docstring.
    """

    def _pack(value: float, offset: float = 0.0) -> tuple[int, int]:
        fixed = max(0, min(0xFFFF, round((value + offset) * 512.0)))
        return (fixed >> 8) & 0xFF, fixed & 0xFF

    hum_hi, hum_lo = _pack(humidity_percent_rh)
    temp_hi, temp_lo = _pack(temperature_c, offset=25.0)
    return [hum_hi, hum_lo, temp_hi, temp_lo]


def decode_alg_result(data: bytes) -> tuple[int, int]:
    """(eCO2 ppm, TVOC ppb) from the first four bytes of `ALG_RESULT_DATA`.

    Raises:
        HardwareError: The data is too short to hold both values.
    """
    if len(data) < 4:
        raise HardwareError(f"CCS811 ALG_RESULT_DATA is {len(data)} bytes, not >= 4")
    co2eq = int.from_bytes(data[0:2], "big")
    tvoc = int.from_bytes(data[2:4], "big")
    return co2eq, tvoc


class Ccs811Sensor:
    """One chip at `address`: the boot sequence, then register reads for a measurement."""

    __slots__ = ("address", "link", "sleep")

    def __init__(self, link: I2cLink, address: int = CCS811_ADDRESS, sleep: bool = True) -> None:
        self.link = link
        self.address = address
        self.sleep = sleep
        """Whether to wait between boot steps; off in a test against a fake."""

    def _status(self) -> int:
        return self.link.read_register(self.address, STATUS, 1)[0]

    def boot(self, drive_mode: int = DRIVE_MODE_1S) -> None:
        """Leaves boot mode and starts periodic measurement. Mandatory before any read.

        Raises:
            HardwareError: The chip never reports app mode, or reports an error on boot.
        """
        status = self._status()
        if status & STATUS_ERROR:
            raise HardwareError(f"CCS811 reports an error at boot: STATUS=0x{status:02x}")
        if not (status & STATUS_FW_MODE):
            self.link.write(self.address, [APP_START])
            if self.sleep:
                time.sleep(0.001)
            status = self._status()
            if not (status & STATUS_FW_MODE):
                raise HardwareError(f"CCS811 did not leave boot mode: STATUS=0x{status:02x}")
        self.link.write_register(self.address, MEAS_MODE, [drive_mode])

    def set_environment(self, humidity_percent_rh: float, temperature_c: float) -> None:
        """Feeds humidity/temperature compensation ahead of the next measurement."""
        self.link.write_register(
            self.address, ENV_DATA, encode_env_data(humidity_percent_rh, temperature_c)
        )

    def measure(self) -> tuple[int, int]:
        """(eCO2 ppm, TVOC ppb): one register read.

        Raises:
            HardwareError: `STATUS` reports an error, or the chip is not in app mode.
        """
        status = self._status()
        if not (status & STATUS_FW_MODE):
            raise HardwareError("CCS811 read attempted before APP_START / boot()")
        if status & STATUS_ERROR:
            error = self.link.read_register(self.address, ERROR_ID, 1)[0]
            raise HardwareError(f"CCS811 reports an error: ERROR_ID=0x{error:02x}")
        return decode_alg_result(self.link.read_register(self.address, ALG_RESULT_DATA, 4))


class Ccs811(Readable):
    """One chip on the device root: `co2eq`, `tvoc` [RP], booted once, a register read per read."""

    co2eq = Output("co2eq", quantity=CO2EQ, access=Access.RP, range=(400.0, 8192.0), precision=0)
    tvoc = Output("tvoc", quantity=TVOC, access=Access.RP, range=(0.0, 1187.0), precision=0)

    def __init__(
        self,
        name: str,
        link: I2cLink,
        address: int = CCS811_ADDRESS,
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.sensor = Ccs811Sensor(link, address, sleep)
        self.sensor.boot()

    @property
    def config(self) -> Ccs811Config:
        return Ccs811Config(link="", address=self.sensor.address)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        co2eq, tvoc = self.sensor.measure()
        yield self.sample(time_ns, co2eq=co2eq, tvoc=tvoc)


class Ccs811Config(DriverConfig[Ccs811], tag="ccs811"):
    """One chip by its I2C address."""

    link: I2cLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(default=CCS811_ADDRESS, ge=0x03, le=0x77)

    def build(self, name: str, label: str | None = None) -> Ccs811:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Ccs811(name, resolve(self.link), self.address, label=label)


Ccs811.config_type = Ccs811Config


__all__ = [
    "ALG_RESULT_DATA",
    "APP_START",
    "BASELINE",
    "CCS811_ADDRESS",
    "CO2EQ",
    "DRIVE_MODE_1S",
    "ENV_DATA",
    "ERROR_ID",
    "HW_ID",
    "HW_ID_VALUE",
    "HW_VERSION",
    "MEAS_MODE",
    "RAW_DATA",
    "STATUS",
    "STATUS_DATA_READY",
    "STATUS_ERROR",
    "STATUS_FW_MODE",
    "SW_RESET",
    "THRESHOLDS",
    "TVOC",
    "Ccs811",
    "Ccs811Config",
    "Ccs811Sensor",
    "decode_alg_result",
    "encode_env_data",
]
