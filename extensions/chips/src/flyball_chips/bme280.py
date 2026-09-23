"""Bosch BME280/BMP280: temperature, pressure and (BME280 only) humidity over I2C.

Decoded from the public Bosch BME280 datasheet (rev 1.6+); this driver has
never been run against real hardware.

The raw ADC registers (`press`, `temp`, `hum`) are a plain register map, but
turning them into physical units needs Bosch's own polynomial compensation
against a block of factory-trimmed calibration words read once at startup --
not [I2cTable][flyball_linux.devices.i2c_table]'s `scale`/`offset`, so this
module reads both blocks directly through [I2cLink][flyball.hardware.i2c.I2cLink]
and applies the datasheet's floating-point compensation formulas verbatim
(section 4.2.3 of the datasheet). The tests hold one input/output pair taken
from Bosch's reference driver (BME280_SensorAPI, compiled): temperature matches
it exactly, pressure to 0.01 Pa, because Bosch truncates `t_fine` to an integer
and this module does not.

Pressure compensation needs `t_fine` from the temperature compensation done
in the same reading, so `read()` always converts temperature first.
BMP280 has no humidity registers or calibration words at all; `has_humidity`
selects which block this driver reads (default: true, i.e. BME280 wiring;
set false for a BMP280 on the same bus).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import NamedTuple

from flyball.foundation.config import resolve
from flyball.foundation.device import Access, DriverConfig, Node, Readable, Sample, SignalSpec
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.dimensions import Fraction
from flyball.foundation.quantities.si import Celsius, Pascal
from flyball.hardware.i2c import I2cLink
from pydantic import Field

from flyball_chips._links import I2cLinkConfig

BME280_ADDRESS = 0x76
"""SDO low; SDO high answers at 0x77."""

CHIP_ID = 0xD0
RESET = 0xE0
_RESET_VALUE = 0xB6
CTRL_HUM = 0xF2
STATUS = 0xF3
CTRL_MEAS = 0xF4
_DATA_BASE = 0xF7  # press_msb..hum_lsb, 8 bytes (6 if no humidity)
_CALIB_T_P = 0x88  # 24 bytes: dig_T1..dig_P9 (0x88..0x9F; 0xA0 is reserved)
_CALIB_H1 = 0xA1  # 1 byte
_CALIB_H2_6 = 0xE1  # 7 bytes: dig_H2..dig_H6, packed

_FORCED_MODE = 0b01
Oversample = int  # 0 (skipped), 1, 2, 4, 8 or 16
_OSR_BITS = {0: 0b000, 1: 0b001, 2: 0b010, 4: 0b011, 8: 0b100, 16: 0b101}

PercentRH = Fraction.unit("percent relative humidity", "%RH", 0.01, scale=(0.0, 100.0))
HUMIDITY = Quantity("humidity", PercentRH)
PRESSURE = Quantity("pressure", Pascal)
TEMPERATURE = Quantity("temperature", Celsius)


class Calibration(NamedTuple):
    """The factory trim words, parsed and sign-extended; `h*` are `None` on a BMP280."""

    dig_t1: int
    dig_t2: int
    dig_t3: int
    dig_p1: int
    dig_p2: int
    dig_p3: int
    dig_p4: int
    dig_p5: int
    dig_p6: int
    dig_p7: int
    dig_p8: int
    dig_p9: int
    dig_h1: int | None = None
    dig_h2: int | None = None
    dig_h3: int | None = None
    dig_h4: int | None = None
    dig_h5: int | None = None
    dig_h6: int | None = None


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little", signed=False)


def _s16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little", signed=True)


def _s8(byte: int) -> int:
    return byte - 256 if byte >= 128 else byte


def parse_calibration(
    t_p_block: bytes, h1_byte: bytes | None, h2_6_block: bytes | None
) -> Calibration:
    """`t_p_block` is the 24 bytes at 0x88; `h1_byte`/`h2_6_block` are `None` on a BMP280."""
    if len(t_p_block) != 24:
        raise ValueError(f"BME280 T/P calibration block is {len(t_p_block)} bytes, not 24")
    dig_t1 = _u16(t_p_block, 0)
    dig_t2 = _s16(t_p_block, 2)
    dig_t3 = _s16(t_p_block, 4)
    dig_p1 = _u16(t_p_block, 6)
    dig_p2 = _s16(t_p_block, 8)
    dig_p3 = _s16(t_p_block, 10)
    dig_p4 = _s16(t_p_block, 12)
    dig_p5 = _s16(t_p_block, 14)
    dig_p6 = _s16(t_p_block, 16)
    dig_p7 = _s16(t_p_block, 18)
    dig_p8 = _s16(t_p_block, 20)
    dig_p9 = _s16(t_p_block, 22)
    if h1_byte is None or h2_6_block is None:
        return Calibration(
            dig_t1,
            dig_t2,
            dig_t3,
            dig_p1,
            dig_p2,
            dig_p3,
            dig_p4,
            dig_p5,
            dig_p6,
            dig_p7,
            dig_p8,
            dig_p9,
        )
    if len(h2_6_block) != 7:
        raise ValueError(f"BME280 H calibration block is {len(h2_6_block)} bytes, not 7")
    dig_h1 = h1_byte[0]
    dig_h2 = _s16(h2_6_block, 0)
    dig_h3 = h2_6_block[2]
    e4, e5, e6 = h2_6_block[3], h2_6_block[4], h2_6_block[5]
    dig_h4 = _sign12((e4 << 4) | (e5 & 0x0F))
    dig_h5 = _sign12((e6 << 4) | (e5 >> 4))
    dig_h6 = _s8(h2_6_block[6])
    return Calibration(
        dig_t1,
        dig_t2,
        dig_t3,
        dig_p1,
        dig_p2,
        dig_p3,
        dig_p4,
        dig_p5,
        dig_p6,
        dig_p7,
        dig_p8,
        dig_p9,
        dig_h1,
        dig_h2,
        dig_h3,
        dig_h4,
        dig_h5,
        dig_h6,
    )


def _sign12(value: int) -> int:
    value &= 0xFFF
    return value - 0x1000 if value & 0x800 else value


def compensate_temperature(adc_t: int, cal: Calibration) -> tuple[float, float]:
    """(°C, t_fine); `t_fine` feeds pressure and humidity compensation."""
    var1 = (adc_t / 16384.0 - cal.dig_t1 / 1024.0) * cal.dig_t2
    var2 = (adc_t / 131072.0 - cal.dig_t1 / 8192.0) ** 2 * cal.dig_t3
    t_fine = var1 + var2
    return t_fine / 5120.0, t_fine


def compensate_pressure(adc_p: int, t_fine: float, cal: Calibration) -> float:
    """Pa, or 0.0 if `dig_p1` is zero (the datasheet's own divide-by-zero guard)."""
    var1 = t_fine / 2.0 - 64000.0
    var2 = var1 * var1 * cal.dig_p6 / 32768.0
    var2 += var1 * cal.dig_p5 * 2.0
    var2 = var2 / 4.0 + cal.dig_p4 * 65536.0
    var1 = (cal.dig_p3 * var1 * var1 / 524288.0 + cal.dig_p2 * var1) / 524288.0
    var1 = (1.0 + var1 / 32768.0) * cal.dig_p1
    if var1 == 0:
        return 0.0
    p = 1048576.0 - adc_p
    p = (p - var2 / 4096.0) * 6250.0 / var1
    var1 = cal.dig_p9 * p * p / 2147483648.0
    var2 = p * cal.dig_p8 / 32768.0
    return p + (var1 + var2 + cal.dig_p7) / 16.0


def compensate_humidity(adc_h: int, t_fine: float, cal: Calibration) -> float:
    """%RH, clamped to 0..100. Requires the H calibration words (BME280 only)."""
    if (
        cal.dig_h1 is None
        or cal.dig_h2 is None
        or cal.dig_h3 is None
        or cal.dig_h4 is None
        or cal.dig_h5 is None
        or cal.dig_h6 is None
    ):
        raise ValueError("no humidity calibration: this is a BMP280 reading, not a BME280")
    var_h = t_fine - 76800.0
    var_h = (adc_h - (cal.dig_h4 * 64.0 + cal.dig_h5 / 16384.0 * var_h)) * (
        cal.dig_h2
        / 65536.0
        * (1.0 + cal.dig_h6 / 67108864.0 * var_h * (1.0 + cal.dig_h3 / 67108864.0 * var_h))
    )
    var_h *= 1.0 - cal.dig_h1 * var_h / 524288.0
    return min(100.0, max(0.0, var_h))


def ctrl_meas(osrs_t: Oversample, osrs_p: Oversample, mode: int = _FORCED_MODE) -> int:
    return (_OSR_BITS[osrs_t] << 5) | (_OSR_BITS[osrs_p] << 2) | mode


def ctrl_hum(osrs_h: Oversample) -> int:
    return _OSR_BITS[osrs_h]


def max_measurement_s(osrs_t: Oversample, osrs_p: Oversample, osrs_h: Oversample) -> float:
    """Datasheet appendix B: worst-case conversion time for these oversamplings."""
    total_ms = 1.25
    if osrs_t:
        total_ms += 2.3 * osrs_t
    if osrs_p:
        total_ms += 2.3 * osrs_p + 0.575
    if osrs_h:
        total_ms += 2.3 * osrs_h + 0.575
    return total_ms / 1000.0


class Bme280Sensor:
    """One chip: calibration read once at init, a forced-mode conversion per `read`."""

    __slots__ = ("address", "cal", "has_humidity", "link", "osrs_h", "osrs_p", "osrs_t", "sleep")

    def __init__(
        self,
        link: I2cLink,
        address: int,
        has_humidity: bool = True,
        osrs_t: Oversample = 1,
        osrs_p: Oversample = 1,
        osrs_h: Oversample = 1,
        sleep: bool = True,
    ) -> None:
        self.link = link
        self.address = address
        self.has_humidity = has_humidity
        self.osrs_t = osrs_t
        self.osrs_p = osrs_p
        self.osrs_h = osrs_h if has_humidity else 0
        self.sleep = sleep
        t_p = self.link.read_register(address, _CALIB_T_P, 24)
        if has_humidity:
            h1 = self.link.read_register(address, _CALIB_H1, 1)
            h2_6 = self.link.read_register(address, _CALIB_H2_6, 7)
            self.cal = parse_calibration(t_p, h1, h2_6)
        else:
            self.cal = parse_calibration(t_p, None, None)

    def read(self) -> tuple[float, float, float | None]:
        """(°C, Pa, %RH or `None`): one forced-mode conversion, one burst read."""
        if self.has_humidity:
            self.link.write_register(self.address, CTRL_HUM, [ctrl_hum(self.osrs_h)])
        self.link.write_register(self.address, CTRL_MEAS, [ctrl_meas(self.osrs_t, self.osrs_p)])
        if self.sleep:
            time.sleep(max_measurement_s(self.osrs_t, self.osrs_p, self.osrs_h))
        length = 8 if self.has_humidity else 6
        data = self.link.read_register(self.address, _DATA_BASE, length)
        adc_p = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
        adc_t = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
        temperature, t_fine = compensate_temperature(adc_t, self.cal)
        pressure = compensate_pressure(adc_p, t_fine, self.cal)
        humidity = None
        if self.has_humidity:
            adc_h = (data[6] << 8) | data[7]
            humidity = compensate_humidity(adc_h, t_fine, self.cal)
        return temperature, pressure, humidity


class Bme280(Readable):
    """One chip on the device root: `temperature`, `pressure`, and `humidity` if wired as a BME280.

    `humidity` is only bound to the tree when `has_humidity` is true -- a
    BMP280 (or a BME280 config with `has_humidity: false`) has neither the
    registers nor the calibration words for it.
    """

    def __init__(
        self,
        name: str,
        link: I2cLink,
        address: int = BME280_ADDRESS,
        has_humidity: bool = True,
        osrs_t: Oversample = 1,
        osrs_p: Oversample = 1,
        osrs_h: Oversample = 1,
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.has_humidity = has_humidity
        specs = [
            SignalSpec(
                name="temperature",
                quantity=TEMPERATURE,
                access=Access.RP,
                range=(-40.0, 85.0),
                precision=2,
            ),
            SignalSpec(
                name="pressure",
                quantity=PRESSURE,
                access=Access.RP,
                range=(30000.0, 110000.0),
                precision=1,
            ),
        ]
        if has_humidity:
            specs.append(
                SignalSpec(
                    name="humidity",
                    quantity=HUMIDITY,
                    access=Access.RP,
                    range=(0.0, 100.0),
                    precision=2,
                )
            )
        self.bind(specs)
        self.sensor = Bme280Sensor(link, address, has_humidity, osrs_t, osrs_p, osrs_h, sleep)

    @property
    def config(self) -> Bme280Config:
        return Bme280Config(
            link="",
            address=self.sensor.address,
            has_humidity=self.has_humidity,
            osrs_t=self.sensor.osrs_t,
            osrs_p=self.sensor.osrs_p,
            osrs_h=self.sensor.osrs_h,
        )

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        temperature, pressure, humidity = self.sensor.read()
        values = {self.signals["temperature"]: temperature, self.signals["pressure"]: pressure}
        if humidity is not None:
            values[self.signals["humidity"]] = humidity
        yield Sample(self.root, time_ns, values)


class Bme280Config(DriverConfig[Bme280], type="bme280"):
    """`has_humidity: false` for a BMP280 (no humidity registers or calibration)."""

    link: I2cLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(default=BME280_ADDRESS, ge=0x03, le=0x77)
    has_humidity: bool = True
    osrs_t: Oversample = 1
    osrs_p: Oversample = 1
    osrs_h: Oversample = 1

    def build(self, name: str, label: str | None = None) -> Bme280:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Bme280(
            name,
            resolve(self.link),
            self.address,
            self.has_humidity,
            self.osrs_t,
            self.osrs_p,
            self.osrs_h,
            label=label,
        )


Bme280.config_type = Bme280Config  # the config is declared after the device it builds


__all__ = [
    "BME280_ADDRESS",
    "HUMIDITY",
    "PRESSURE",
    "TEMPERATURE",
    "Bme280",
    "Bme280Config",
    "Bme280Sensor",
    "Calibration",
    "compensate_humidity",
    "compensate_pressure",
    "compensate_temperature",
    "ctrl_hum",
    "ctrl_meas",
    "max_measurement_s",
    "parse_calibration",
]
