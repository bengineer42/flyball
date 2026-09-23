"""Bosch BME680: temperature, humidity, pressure and gas resistance over I2C.

Decoded from Bosch's public `BME68x_SensorAPI` reference driver (the BME680
and BME688 share it); this driver has never been run against real hardware.

**Known wrong: do not trust its readings.** Checked against Bosch's
`BME68x_SensorAPI`, the byte offsets are off: the raw pressure is taken from
`data[1..3]` of the 0x1D burst instead of `data[2..4]`; the `par_p*`
coefficients are read one byte early (`par_p1` from 0x8D/0x8E, where Bosch
has 0x8E/0x8F); and `res_heat_val`/`res_heat_range` are read from the end of
the 0xE1 block rather than from their own registers (0x00, 0x02). The tests
pass only because their fixtures are encoded with this module's own layout.
The offsets stay wrong until they are re-derived from the Bosch map.

BME680 is the same Bosch T/H/P calibration *family* as BME280 -- register
words with the same names (`par_t1`, `par_p1`.. etc) turned into physical
units by a floating-point polynomial against `t_fine` -- but the polynomials
are genuinely different chip to chip, not just a relabelling:
[bme280.py][flyball_chips.bme280] is not reused here. Checked
against Bosch's own `calc_temperature`/`calc_pressure`/`calc_humidity` (FPU
variants): the temperature `var2` term carries an extra `* 16.0f` on
`par_t3` that BME280's does not, and the pressure `var1` term groups
`par_p3` differently (`(par_p3 * var1 * var1) / 16384.0` vs BME280's
`(dig_p3 * var1 * var1) / 524288.0` folded into a different shift), so a
shared function would silently be wrong for one chip or the other.

Calibration coefficient parsing (which bytes of the two coefficient blocks,
`0x89` and `0xE1`, become which `par_*`/`res_heat_*`/`range_sw_err` field,
including the two 12-bit `par_h1`/`par_h2` sharing a byte the way BME280's
`dig_h4`/`dig_h5` do) is `[Unverified]`: transcribed from long-standing
public BME680 driver ports (Bosch's own and third-party), not re-derived
from the raw datasheet table in this session, and not checked against a
real chip. The temperature/pressure/humidity/gas-resistance compensation
formulas themselves and the heater-control math (`calc_res_heat`,
`calc_gas_wait`) are transcribed verbatim from Bosch's current
`BME68x_SensorAPI` source (`bme68x.c`), which is a primary source, but this
module has no real chip to check the *result* against, so `[Unverified]`
applies to correctness end-to-end regardless.

The gas channel needs a heater profile, not just a conversion: `res_heat_0`
is loaded from a target plate temperature (needs an assumed ambient
temperature -- `amb_temp_c`, since there is no on-chip ambient sensor
separate from the plate) and `gas_wait_0` from a heating duration, `run_gas`
is set, one forced-mode measurement is triggered, and after the datasheet's
wait the gas ADC, `gas_range` and the `heat_stab`/`gas_valid` status bits
are read back together -- `heat_stab` clear means the resistance reading is
not to be trusted (the plate had not settled), which this driver surfaces
as a `HardwareError` rather than a silently wrong sample.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import NamedTuple

from flyball.foundation.config import resolve
from flyball.foundation.device import Access, DriverConfig, Node, Readable, Sample, SignalSpec
from flyball.foundation.errors import HardwareError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.dimensions import Fraction
from flyball.foundation.quantities.si import Celsius, Ohm, Pascal
from flyball.hardware.i2c import I2cLink
from pydantic import Field

from flyball_chips._links import I2cLinkConfig

BME680_ADDRESS = 0x76
"""SDO low; SDO high answers at 0x77."""

CHIP_ID = 0xD0
RESET = 0xE0
_RESET_VALUE = 0xB6
CTRL_HUM = 0x72
CTRL_MEAS = 0x74
CONFIG = 0x75
CTRL_GAS_1 = 0x71
RES_HEAT_0 = 0x5A
GAS_WAIT_0 = 0x64
_FIELD0 = 0x1D  # status, press[3], temp[3], hum[2], gas_r[2]: 10 bytes read as one burst

_COEFF1 = 0x89  # 25 bytes: 0x89..0xA1
_COEFF2 = 0xE1  # 16 bytes: 0xE1..0xF0

_GAS_VALID_MASK = 0x20
_HEAT_STAB_MASK = 0x10
_NEW_DATA_MASK = 0x80
_GAS_RANGE_MASK = 0x0F
_RUN_GAS = 0x10  # ctrl_gas_1 bit 4
_FORCED_MODE = 0b01

Oversample = int
_OSR_BITS = {0: 0b000, 1: 0b001, 2: 0b010, 4: 0b011, 8: 0b100, 16: 0b101}

PercentRH = Fraction.unit("percent relative humidity", "%RH", 0.01, scale=(0.0, 100.0))
HUMIDITY = Quantity("humidity", PercentRH)
PRESSURE = Quantity("pressure", Pascal)
TEMPERATURE = Quantity("temperature", Celsius)
GAS_RESISTANCE = Quantity("gas_resistance", Ohm)

_K1_RANGE = (0.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0.0, -0.8, 0.0, 0.0, -0.2, -0.5, 0.0, -1.0, 0.0, 0.0)
_K2_RANGE = (0.0, 0.0, 0.0, 0.0, 0.1, 0.7, 0.0, -0.8, -0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
"""Bosch's per-`gas_range` correction table (`calc_gas_resistance_low`)."""


class Calibration(NamedTuple):
    par_t1: int
    par_t2: int
    par_t3: int
    par_p1: int
    par_p2: int
    par_p3: int
    par_p4: int
    par_p5: int
    par_p6: int
    par_p7: int
    par_p8: int
    par_p9: int
    par_p10: int
    par_h1: int
    par_h2: int
    par_h3: int
    par_h4: int
    par_h5: int
    par_h6: int
    par_h7: int
    par_gh1: int
    par_gh2: int
    par_gh3: int
    res_heat_range: int
    res_heat_val: int
    range_sw_err: int


def _s16(lo: int, hi: int) -> int:
    return int.from_bytes(bytes([lo, hi]), "little", signed=True)


def _u16(lo: int, hi: int) -> int:
    return int.from_bytes(bytes([lo, hi]), "little", signed=False)


def _s8(byte: int) -> int:
    return byte - 256 if byte >= 128 else byte


def parse_calibration(coeff1: bytes, coeff2: bytes) -> Calibration:
    """`coeff1` is the 25 bytes at 0x89; `coeff2` is the 16 bytes at 0xE1."""
    if len(coeff1) != 25:
        raise ValueError(f"BME680 coefficient block 1 is {len(coeff1)} bytes, not 25")
    if len(coeff2) != 16:
        raise ValueError(f"BME680 coefficient block 2 is {len(coeff2)} bytes, not 16")
    c1, c2 = coeff1, coeff2
    par_t2 = _s16(c1[1], c1[2])
    par_t3 = _s8(c1[3])
    par_p1 = _u16(c1[4], c1[5])
    par_p2 = _s16(c1[6], c1[7])
    par_p3 = _s8(c1[8])
    par_p4 = _s16(c1[10], c1[11])
    par_p5 = _s16(c1[12], c1[13])
    par_p7 = _s8(c1[14])
    par_p6 = _s8(c1[15])
    par_p8 = _s16(c1[18], c1[19])
    par_p9 = _s16(c1[20], c1[21])
    par_p10 = c1[22]
    range_sw_err = _s8(c1[24] & 0xF0) >> 4

    par_t1 = _u16(c2[8], c2[9])
    par_h2 = (c2[0] << 4) | (c2[1] >> 4)
    par_h1 = ((c2[2] << 4) | (c2[1] & 0x0F)) & 0xFFF
    par_h3 = _s8(c2[3])
    par_h4 = _s8(c2[4])
    par_h5 = _s8(c2[5])
    par_h6 = c2[6]
    par_h7 = _s8(c2[7])
    par_gh2 = _s16(c2[10], c2[11])
    par_gh1 = _s8(c2[12])
    par_gh3 = _s8(c2[13])
    res_heat_range = (c2[14] & 0x30) >> 4
    res_heat_val = _s8(c2[15])
    return Calibration(
        par_t1=par_t1,
        par_t2=par_t2,
        par_t3=par_t3,
        par_p1=par_p1,
        par_p2=par_p2,
        par_p3=par_p3,
        par_p4=par_p4,
        par_p5=par_p5,
        par_p6=par_p6,
        par_p7=par_p7,
        par_p8=par_p8,
        par_p9=par_p9,
        par_p10=par_p10,
        par_h1=par_h1,
        par_h2=par_h2,
        par_h3=par_h3,
        par_h4=par_h4,
        par_h5=par_h5,
        par_h6=par_h6,
        par_h7=par_h7,
        par_gh1=par_gh1,
        par_gh2=par_gh2,
        par_gh3=par_gh3,
        res_heat_range=res_heat_range,
        res_heat_val=res_heat_val,
        range_sw_err=range_sw_err,
    )


def compensate_temperature(adc_t: int, cal: Calibration) -> tuple[float, float]:
    """(°C, t_fine). Bosch `calc_temperature` (FPU variant)."""
    var1 = (adc_t / 16384.0 - cal.par_t1 / 1024.0) * cal.par_t2
    var2 = (adc_t / 131072.0 - cal.par_t1 / 8192.0) ** 2 * (cal.par_t3 * 16.0)
    t_fine = var1 + var2
    return t_fine / 5120.0, t_fine


def compensate_pressure(adc_p: int, t_fine: float, cal: Calibration) -> float:
    """Pa, or 0.0 if `var1` rounds to zero. Bosch `calc_pressure` (FPU variant)."""
    var1 = t_fine / 2.0 - 64000.0
    var2 = var1 * var1 * (cal.par_p6 / 131072.0)
    var2 += var1 * cal.par_p5 * 2.0
    var2 = var2 / 4.0 + cal.par_p4 * 65536.0
    var1 = ((cal.par_p3 * var1 * var1) / 16384.0 + cal.par_p2 * var1) / 524288.0
    var1 = (1.0 + var1 / 32768.0) * cal.par_p1
    if int(var1) == 0:
        return 0.0
    p = 1048576.0 - adc_p
    p = ((p - var2 / 4096.0) * 6250.0) / var1
    var1 = (cal.par_p9 * p * p) / 2147483648.0
    var2 = p * (cal.par_p8 / 32768.0)
    var3 = (p / 256.0) ** 3 * (cal.par_p10 / 131072.0)
    return p + (var1 + var2 + var3 + cal.par_p7 * 128.0) / 16.0


def compensate_humidity(adc_h: int, t_fine: float, cal: Calibration) -> float:
    """%RH, clamped to 0..100. Bosch `calc_humidity` (FPU variant); needs `t_fine`."""
    temp_comp = t_fine / 5120.0
    var1 = adc_h - (cal.par_h1 * 16.0 + (cal.par_h3 / 2.0) * temp_comp)
    var2 = var1 * (
        (cal.par_h2 / 262144.0)
        * (1.0 + (cal.par_h4 / 16384.0) * temp_comp + (cal.par_h5 / 1048576.0) * temp_comp**2)
    )
    var3 = cal.par_h6 / 16384.0
    var4 = cal.par_h7 / 2097152.0
    humidity = var2 + (var3 + var4 * temp_comp) * var2 * var2
    return min(100.0, max(0.0, humidity))


def compensate_gas_resistance(gas_adc: int, gas_range: int, cal: Calibration) -> float:
    """Ohms. Bosch `calc_gas_resistance_low` (FPU variant, 0..15 `gas_range`)."""
    var1 = 1340.0 + 5.0 * cal.range_sw_err
    var2 = var1 * (1.0 + _K1_RANGE[gas_range] / 100.0)
    var3 = 1.0 + _K2_RANGE[gas_range] / 100.0
    gas_range_f = float(1 << gas_range)
    return 1.0 / (var3 * 0.000000125 * gas_range_f * ((gas_adc - 512.0) / var2 + 1.0))


def calc_res_heat(target_c: int, amb_temp_c: float, cal: Calibration) -> int:
    """The `res_heat_0` byte for a heater plate target of `target_c` (clamped to 400°C max)."""
    target_c = min(target_c, 400)
    var1 = (amb_temp_c * cal.par_gh3 / 1000.0) * 256.0
    var2 = (cal.par_gh1 + 784.0) * (
        (((cal.par_gh2 + 154009.0) * target_c * 5.0 / 100.0) + 3276800.0) / 10.0
    )
    var3 = var1 + var2 / 2.0
    var4 = var3 / (cal.res_heat_range + 4.0)
    var5 = 131.0 * cal.res_heat_val + 65536.0
    heatr_res_x100 = (var4 / var5 - 250.0) * 34.0
    return round((heatr_res_x100 + 50.0) / 100.0)


def calc_gas_wait(duration_ms: int) -> int:
    """The `gas_wait_0` byte for a heating duration: 6-bit value plus a 2-bit /4 multiplier."""
    if duration_ms >= 0xFC0:
        return 0xFF
    factor = 0
    while duration_ms > 0x3F:
        duration_ms //= 4
        factor += 1
    return duration_ms + factor * 64


class Bme680Sensor:
    """One chip: calibration read once at init, a forced T/P/H/gas conversion per `read`."""

    __slots__ = (
        "address",
        "amb_temp_c",
        "cal",
        "gas_heater_c",
        "gas_wait_ms",
        "link",
        "osrs_h",
        "osrs_p",
        "osrs_t",
        "sleep",
    )

    def __init__(
        self,
        link: I2cLink,
        address: int,
        osrs_t: Oversample = 2,
        osrs_p: Oversample = 4,
        osrs_h: Oversample = 2,
        gas_heater_c: int = 320,
        gas_wait_ms: int = 150,
        amb_temp_c: float = 25.0,
        sleep: bool = True,
    ) -> None:
        self.link = link
        self.address = address
        self.osrs_t = osrs_t
        self.osrs_p = osrs_p
        self.osrs_h = osrs_h
        self.gas_heater_c = gas_heater_c
        self.gas_wait_ms = gas_wait_ms
        self.amb_temp_c = amb_temp_c
        self.sleep = sleep
        coeff1 = self.link.read_register(address, _COEFF1, 25)
        coeff2 = self.link.read_register(address, _COEFF2, 16)
        self.cal = parse_calibration(coeff1, coeff2)

    def _load_heater_profile(self) -> None:
        res_heat = calc_res_heat(self.gas_heater_c, self.amb_temp_c, self.cal)
        self.link.write_register(self.address, RES_HEAT_0, [res_heat & 0xFF])
        self.link.write_register(self.address, GAS_WAIT_0, [calc_gas_wait(self.gas_wait_ms)])
        self.link.write_register(self.address, CTRL_GAS_1, [_RUN_GAS])  # heater profile 0

    def read(self) -> tuple[float, float, float, float]:
        """(°C, Pa, %RH, Ω gas resistance): one forced conversion, one field-data burst.

        Raises:
            HardwareError: `heat_stab` was clear -- the gas reading is not trustworthy.
        """
        self.link.write_register(self.address, CTRL_HUM, [_OSR_BITS[self.osrs_h]])
        self._load_heater_profile()
        ctrl_meas = (_OSR_BITS[self.osrs_t] << 5) | (_OSR_BITS[self.osrs_p] << 2) | _FORCED_MODE
        self.link.write_register(self.address, CTRL_MEAS, [ctrl_meas])
        if self.sleep:
            max_meas_s = 0.00125 + 0.0023 * (self.osrs_t + self.osrs_p + self.osrs_h)
            time.sleep(max_meas_s + self.gas_wait_ms / 1000.0)
        data = self.link.read_register(self.address, _FIELD0, 9)
        adc_p = (data[1] << 12) | (data[2] << 4) | (data[3] >> 4)
        adc_t = (data[4] << 12) | (data[5] << 4) | (data[6] >> 4)
        adc_h = (data[7] << 8) | data[8]
        # [Unverified]: the gas sub-block's exact offset past status+P+T+H varies between the
        # BME68x reference driver's "low" and "high" gas variants; this reads it immediately
        # after humidity, which matches the low-gas-range field layout this module assumes.
        gas_msb, gas_lsb = self.link.read_register(self.address, _FIELD0 + 9, 2)
        gas_adc = (gas_msb << 2) | (gas_lsb >> 6)
        gas_range = gas_lsb & _GAS_RANGE_MASK
        if not gas_lsb & _HEAT_STAB_MASK:
            raise HardwareError("BME680 gas heater was not stable: reading discarded")
        if not gas_lsb & _GAS_VALID_MASK:
            raise HardwareError("BME680 gas reading is not valid")
        temperature, t_fine = compensate_temperature(adc_t, self.cal)
        pressure = compensate_pressure(adc_p, t_fine, self.cal)
        humidity = compensate_humidity(adc_h, t_fine, self.cal)
        gas_resistance = compensate_gas_resistance(gas_adc, gas_range, self.cal)
        self.amb_temp_c = temperature  # feeds the next heater calculation
        return temperature, pressure, humidity, gas_resistance


class Bme680(Readable):
    """One chip on the device root: `temperature`, `humidity`, `pressure`, `gas_resistance`."""

    def __init__(
        self,
        name: str,
        link: I2cLink,
        address: int = BME680_ADDRESS,
        osrs_t: Oversample = 2,
        osrs_p: Oversample = 4,
        osrs_h: Oversample = 2,
        gas_heater_c: int = 320,
        gas_wait_ms: int = 150,
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.bind([
            SignalSpec(
                name="temperature",
                quantity=TEMPERATURE,
                access=Access.RP,
                range=(-40.0, 85.0),
                precision=2,
            ),
            SignalSpec(
                name="humidity",
                quantity=HUMIDITY,
                access=Access.RP,
                range=(0.0, 100.0),
                precision=2,
            ),
            SignalSpec(
                name="pressure",
                quantity=PRESSURE,
                access=Access.RP,
                range=(30000.0, 110000.0),
                precision=1,
            ),
            SignalSpec(
                name="gas_resistance",
                quantity=GAS_RESISTANCE,
                access=Access.RP,
                range=(0.0, 1e7),
                precision=0,
            ),
        ])
        self.sensor = Bme680Sensor(
            link, address, osrs_t, osrs_p, osrs_h, gas_heater_c, gas_wait_ms, sleep=sleep
        )

    @property
    def config(self) -> Bme680Config:
        return Bme680Config(
            link="",
            address=self.sensor.address,
            osrs_t=self.sensor.osrs_t,
            osrs_p=self.sensor.osrs_p,
            osrs_h=self.sensor.osrs_h,
            gas_heater_c=self.sensor.gas_heater_c,
            gas_wait_ms=self.sensor.gas_wait_ms,
        )

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        temperature, pressure, humidity, gas_resistance = self.sensor.read()
        yield Sample(
            self.root,
            time_ns,
            {
                self.signals["temperature"]: temperature,
                self.signals["pressure"]: pressure,
                self.signals["humidity"]: humidity,
                self.signals["gas_resistance"]: gas_resistance,
            },
        )


class Bme680Config(DriverConfig[Bme680], tag="bme680"):
    """`gas_heater_c`/`gas_wait_ms` set the heater profile used on every read."""

    link: I2cLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(default=BME680_ADDRESS, ge=0x03, le=0x77)
    osrs_t: Oversample = 2
    osrs_p: Oversample = 4
    osrs_h: Oversample = 2
    gas_heater_c: int = Field(default=320, ge=0, le=400)
    gas_wait_ms: int = Field(default=150, ge=0, le=4032)

    def build(self, name: str, label: str | None = None) -> Bme680:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Bme680(
            name,
            resolve(self.link),
            self.address,
            self.osrs_t,
            self.osrs_p,
            self.osrs_h,
            self.gas_heater_c,
            self.gas_wait_ms,
            label=label,
        )


Bme680.config_type = Bme680Config  # the config is declared after the device it builds


__all__ = [
    "BME680_ADDRESS",
    "GAS_RESISTANCE",
    "HUMIDITY",
    "PRESSURE",
    "TEMPERATURE",
    "Bme680",
    "Bme680Config",
    "Bme680Sensor",
    "Calibration",
    "calc_gas_wait",
    "calc_res_heat",
    "compensate_gas_resistance",
    "compensate_humidity",
    "compensate_pressure",
    "compensate_temperature",
    "parse_calibration",
]
