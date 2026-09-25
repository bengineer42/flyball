"""Maxim MAX31856: precision thermocouple to digital, SPI, register-addressed.

Uses SPI mode 1 or 3 (CPHA=1) -- set on the `spi` link's own `spi_mode:`, not a
field here; this driver only speaks the byte protocol.

Registers (datasheet "Register Map", confirmed against the Adafruit
MAX31856 library's register constants):

| address | name | what |
| --- | --- | --- |
| 0x00 | CR0 | conversion mode, open-circuit fault detection, 50/60 Hz filter |
| 0x01 | CR1 | averaging (`AVGSEL`), thermocouple type (`TC_TYPE`) |
| 0x0A/0x0B | CJTH/CJTL | cold-junction temperature: signed 14-bit field in bits 15:2, 2⁻⁶ °C/LSB |
| 0x0C-0x0E | LTCBH/M/L | linearised temperature: signed 19-bit field in bits 23:5, 2⁻⁷ °C/LSB |
| 0x0F | SR | fault status |

`driver: {thermocouple_type, averaging, filter_hz, conversion}` sets CR0/CR1
once at build: `conversion: continuous` runs the chip's own automatic-conversion loop
(CR0 D7) and each read is whatever it last converted; `conversion: one_shot`
triggers a conversion (CR0 D6) and waits for it before reading. Open-circuit
fault detection is enabled at its simplest setting (CR0 D5:D4 = 01, the
datasheet's "Mode 1", suited to a bare thermocouple with no parallel RC
filter) [Unverified: whether Mode 1 vs Mode 2/3 is the better default for
an arbitrary rig's wiring -- Mode 1 is the datasheet's own first example].

Conversion time (one-shot, `averaging: 1`) is ~143 ms at 60 Hz / ~169 ms at
50 Hz, reported by users against the datasheet; scaling that linearly with
`averaging` is this driver's own approximation, not a table taken from the
datasheet [Unverified beyond `averaging: 1`].

A fault reads its signal `invalid(reason)`, `reason` naming every set fault
bit for that signal (thermocouple: `open_circuit`, `over_under_voltage`,
`low`, `high`, `range`; cold junction: `low`, `high`, `range`), never a
raise: the chip answered.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Literal

from flyball.foundation.config import resolve
from flyball.foundation.device import DriverConfig, Node, Readable, Readout, Sample, Value, invalid
from flyball.hardware.spi import SpiLink
from pydantic import Field

from flyball_chips._links import SpiLinkConfig
from flyball_chips._max318xx import (
    COLD_JUNCTION,
    TEMPERATURE,
    read_registers,
    sign_extend,
    write_register,
)

ThermocoupleType = Literal["B", "E", "J", "K", "N", "R", "S", "T"]
Averaging = Literal[1, 2, 4, 8, 16]
FilterHz = Literal[50, 60]
Conversion = Literal["continuous", "one_shot"]

CR0 = 0x00
CR1 = 0x01
CJTH = 0x0A
SR = 0x0F

CMODE_BIT = 0x80
ONE_SHOT_BIT = 0x40
OCFAULT_MODE_1 = 0x10
FILTER_50HZ_BIT = 0x01

TC_TYPE_CODE: dict[ThermocoupleType, int] = {
    "B": 0x0,
    "E": 0x1,
    "J": 0x2,
    "K": 0x3,
    "N": 0x4,
    "R": 0x5,
    "S": 0x6,
    "T": 0x7,
}
AVERAGING_CODE: dict[Averaging, int] = {1: 0b000, 2: 0b001, 4: 0b010, 8: 0b011, 16: 0b100}

BASE_CONVERSION_S = {60: 0.143, 50: 0.169}
"""One-shot, one sample; see the module docstring's [Unverified] note on scaling by averaging."""

COLD_JUNCTION_LSB = 2**-6
LINEARIZED_LSB = 2**-7

TC_FAULT_BITS = {
    0x40: "range",
    0x08: "high",
    0x04: "low",
    0x02: "over_under_voltage",
    0x01: "open_circuit",
}
CJ_FAULT_BITS = {0x80: "range", 0x20: "high", 0x10: "low"}


def _reasons(status: int, bits: dict[int, str]) -> str:
    return "+".join(reason for bit, reason in bits.items() if status & bit)


def cr0(filter_hz: FilterHz, conversion: Conversion) -> int:
    """CR0: OCFAULT mode 1, the filter bit, and (for `continuous`) CMODE."""
    value = OCFAULT_MODE_1
    if filter_hz == 50:
        value |= FILTER_50HZ_BIT
    if conversion == "continuous":
        value |= CMODE_BIT
    return value


def cr1(thermocouple_type: ThermocoupleType, averaging: Averaging) -> int:
    return (AVERAGING_CODE[averaging] << 4) | TC_TYPE_CODE[thermocouple_type]


def decode_cold_junction(msb: int, lsb: int) -> float:
    raw = (msb << 8 | lsb) >> 2
    return sign_extend(raw, 14) * COLD_JUNCTION_LSB


def decode_linearized(msb: int, mid: int, lsb: int) -> float:
    raw = (msb << 16 | mid << 8 | lsb) >> 5
    return sign_extend(raw, 19) * LINEARIZED_LSB


def decode(registers: bytes) -> tuple[Value, Value]:
    """(thermocouple °C or `invalid`, cold-junction °C or `invalid`) from registers 0x00-0x0F."""
    status = registers[SR]
    cold_junction: Value = decode_cold_junction(registers[CJTH], registers[CJTH + 1])
    thermocouple: Value = decode_linearized(registers[0x0C], registers[0x0D], registers[0x0E])
    if status & sum(CJ_FAULT_BITS):
        cold_junction = invalid(_reasons(status, CJ_FAULT_BITS))
    if status & sum(TC_FAULT_BITS):
        thermocouple = invalid(_reasons(status, TC_FAULT_BITS))
    return thermocouple, cold_junction


class Max31856(Readable):
    """One chip: `temperature [RP]` (linearised) and `cold_junction [RP]`."""

    temperature = Readout("temperature", quantity=TEMPERATURE, precision=4)
    cold_junction = Readout("cold_junction", quantity=COLD_JUNCTION, precision=4)

    def __init__(
        self,
        name: str,
        link: SpiLink,
        thermocouple_type: ThermocoupleType = "K",
        averaging: Averaging = 1,
        filter_hz: FilterHz = 60,
        conversion: Conversion = "continuous",
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.thermocouple_type: ThermocoupleType = thermocouple_type
        self.averaging: Averaging = averaging
        self.filter_hz: FilterHz = filter_hz
        self.conversion: Conversion = conversion
        self.sleep = sleep
        """Whether to wait the conversion time in one-shot mode; off in a test against a fake."""
        self._cr0 = cr0(filter_hz, conversion)
        write_register(link, CR0, self._cr0)
        write_register(link, CR1, cr1(thermocouple_type, averaging))

    @property
    def config(self) -> Max31856Config:
        return Max31856Config(
            link="",
            thermocouple_type=self.thermocouple_type,
            averaging=self.averaging,
            filter_hz=self.filter_hz,
            conversion=self.conversion,
        )

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        if self.conversion == "one_shot":
            write_register(self.link, CR0, self._cr0 | ONE_SHOT_BIT)
            if self.sleep:
                time.sleep(BASE_CONVERSION_S[self.filter_hz] * self.averaging)
        temperature, cold_junction = decode(read_registers(self.link, CR0, 0x10))
        yield self.sample(time_ns, temperature=temperature, cold_junction=cold_junction)


class Max31856Config(DriverConfig[Max31856], type="max31856"):
    """`driver: max31856`: a precision thermocouple, register-configured, over SPI (mode 1 or 3)."""

    link: SpiLinkConfig | str  # type: ignore[valid-type]
    thermocouple_type: ThermocoupleType = "K"
    averaging: Averaging = 1
    filter_hz: FilterHz = Field(default=60, description="Mains frequency to reject.")
    conversion: Conversion = "continuous"

    def build(self, name: str, label: str | None = None) -> Max31856:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Max31856(
            name,
            resolve(self.link),
            self.thermocouple_type,
            self.averaging,
            self.filter_hz,
            self.conversion,
            label=label,
        )


Max31856.config_type = Max31856Config  # the config is declared after the device it builds


__all__ = [
    "AVERAGING_CODE",
    "CJ_FAULT_BITS",
    "TC_FAULT_BITS",
    "TC_TYPE_CODE",
    "Averaging",
    "Conversion",
    "FilterHz",
    "Max31856",
    "Max31856Config",
    "ThermocoupleType",
    "cr0",
    "cr1",
    "decode",
    "decode_cold_junction",
    "decode_linearized",
]
