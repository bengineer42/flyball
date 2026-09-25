"""Maxim MAX31865: single-channel RTD (PT100/PT1000) to digital, SPI, register-addressed.

Registers (datasheet "Register Map", confirmed against the Adafruit
MAX31865 library's register constants):

| address | name | what |
| --- | --- | --- |
| 0x00 | Configuration | VBIAS, conversion mode, 3-wire, fault detection, 50/60 Hz filter |
| 0x01/0x02 | RTD MSB/LSB | the 15-bit ratio (D15:D1) plus a fault flag (D0) |
| 0x07 | Fault Status | which fault, once D0 above is set |

Continuous automatic-conversion mode (config D6=1) with VBIAS always on
(config D7=1): the chip free-runs and the latest ratio is always in the
RTD registers when polled -- the same posture `current_loop` takes towards
its wrapped ADC, and it avoids VBIAS settling time on every read. 3-wire
compensation is `wires: 3` (config D4); 2- and 4-wire are electrically
distinct out on the RTD but identical to the chip's own register, so there
is no bit for them.

A set fault flag (RTD LSB D0) reads both signals `invalid(reason)`, `reason`
from the fault status register (`high_threshold`, `low_threshold`,
`refin_low`, `refin_high`, `rtdin_low`, `over_under_voltage`), never a
raise: the chip answered. The fault status register latches until cleared,
so a fault read also writes the config register's clear-fault bit (D1) so
the next read can recover.

Resistance -> temperature is the Callendar-Van Dusen equation with the
IEC 60751 platinum coefficients: `A = 3.9083e-3` °C⁻¹, `B = -5.775e-7`
°C⁻², `C = -4.183e-12` °C⁻⁴ (the last used only below 0°C). For T >= 0,
`R(T) = R0(1 + A·T + B·T²)` inverts to a quadratic in T, solved closed
form. Below 0°C the C term makes it a quartic with no closed form; this
driver takes the T >= 0 solve as a seed and runs eight Newton iterations on
the full cubic-augmented polynomial -- the two-region approach Maxim's and
TI's RTD application notes describe (e.g. TI SBAA275). Round-tripped at
0/100/-50°C for PT100 in the tests; well converged (sub-µ°C) in far fewer
than 8 iterations since the C term's contribution is small.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Literal

from flyball.foundation.config import resolve
from flyball.foundation.device import DriverConfig, Node, Readable, Readout, Sample, invalid
from flyball.hardware.spi import SpiLink
from pydantic import Field

from flyball_chips._links import SpiLinkConfig
from flyball_chips._max318xx import RESISTANCE, TEMPERATURE, read_registers, write_register

RtdType = Literal["pt100", "pt1000"]
Wires = Literal[2, 3, 4]
FilterHz = Literal[50, 60]

R0_OHMS: dict[RtdType, float] = {"pt100": 100.0, "pt1000": 1000.0}

CONFIG = 0x00
RTD_MSB = 0x01
FAULT_STATUS = 0x07

VBIAS_BIT = 0x80
AUTO_CONVERT_BIT = 0x40
THREE_WIRE_BIT = 0x10
FAULT_CLEAR_BIT = 0x02
FILTER_50HZ_BIT = 0x01

FAULT_FLAG_BIT = 0x0001
"""RTD LSB D0: a fault is latched; read the fault status register."""

FAULT_BITS = {
    0x80: "high_threshold",
    0x40: "low_threshold",
    0x20: "refin_low",
    0x10: "refin_high",
    0x08: "rtdin_low",
    0x04: "over_under_voltage",
}

RTD_A = 3.9083e-3
"""°C⁻¹, IEC 60751 Callendar-Van Dusen (platinum, alpha=0.00385)."""
RTD_B = -5.775e-7
"""°C⁻²."""
RTD_C = -4.183e-12
"""°C⁻⁴, the below-0°C cubic term."""


def config(wires: Wires, filter_hz: FilterHz) -> int:
    value = VBIAS_BIT | AUTO_CONVERT_BIT
    if wires == 3:
        value |= THREE_WIRE_BIT
    if filter_hz == 50:
        value |= FILTER_50HZ_BIT
    return value


def temperature_to_resistance(temperature: float, r0: float) -> float:
    """R(T), the Callendar-Van Dusen equation's forward direction (for encoding a fake reply)."""
    if temperature >= 0:
        return r0 * (1 + RTD_A * temperature + RTD_B * temperature**2)
    return r0 * (
        1
        + RTD_A * temperature
        + RTD_B * temperature**2
        + RTD_C * (temperature - 100) * temperature**3
    )


def resistance_to_temperature(resistance: float, r0: float) -> float:
    """T from R: closed-form quadratic for T >= 0, Newton's method below (see module docstring)."""
    ratio = resistance / r0
    discriminant = RTD_A**2 - 4 * RTD_B * (1 - ratio)
    temperature = (-RTD_A + math.sqrt(discriminant)) / (2 * RTD_B)
    if temperature >= 0:
        return temperature
    for _ in range(8):
        f = (
            r0
            * (
                1
                + RTD_A * temperature
                + RTD_B * temperature**2
                + RTD_C * (temperature - 100) * temperature**3
            )
            - resistance
        )
        df = r0 * (
            RTD_A + 2 * RTD_B * temperature + RTD_C * (4 * temperature**3 - 300 * temperature**2)
        )
        temperature -= f / df
    return temperature


def _fault_reason(status: int) -> str:
    return "+".join(reason for bit, reason in FAULT_BITS.items() if status & bit) or "fault"


class Max31865(Readable):
    """One RTD: `resistance [RP]` (Ω) and `temperature [RP]` (°C)."""

    resistance = Readout("resistance", quantity=RESISTANCE, precision=3)
    temperature = Readout("temperature", quantity=TEMPERATURE, precision=4)

    def __init__(
        self,
        name: str,
        link: SpiLink,
        rtd_type: RtdType = "pt100",
        ref_resistor: float = 430.0,
        wires: Wires = 2,
        filter_hz: FilterHz = 60,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.rtd_type: RtdType = rtd_type
        self.ref_resistor = ref_resistor
        self.wires: Wires = wires
        self.filter_hz: FilterHz = filter_hz
        self.r0 = R0_OHMS[rtd_type]
        self._config = config(wires, filter_hz)
        write_register(link, CONFIG, self._config)

    @property
    def config(self) -> Max31865Config:
        return Max31865Config(
            link="",
            rtd_type=self.rtd_type,
            ref_resistor=self.ref_resistor,
            wires=self.wires,
            filter_hz=self.filter_hz,
        )

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        msb, lsb = read_registers(self.link, RTD_MSB, 2)
        raw = (msb << 8) | lsb
        if raw & FAULT_FLAG_BIT:
            (status,) = read_registers(self.link, FAULT_STATUS, 1)
            write_register(self.link, CONFIG, self._config | FAULT_CLEAR_BIT)
            reason = _fault_reason(status)
            yield self.sample(time_ns, resistance=invalid(reason), temperature=invalid(reason))
            return
        resistance = (raw >> 1) / 32768.0 * self.ref_resistor
        temperature = resistance_to_temperature(resistance, self.r0)
        yield self.sample(time_ns, resistance=resistance, temperature=temperature)


class Max31865Config(DriverConfig[Max31865], type="max31865"):
    """`driver: max31865`: a PT100/PT1000 RTD, register-configured, over SPI."""

    link: SpiLinkConfig | str  # type: ignore[valid-type]
    rtd_type: RtdType = "pt100"
    ref_resistor: float = Field(default=430.0, gt=0, description="The precision bias resistor.")
    wires: Wires = 2
    filter_hz: FilterHz = Field(default=60, description="Mains frequency to reject.")

    def build(self, name: str, label: str | None = None) -> Max31865:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Max31865(
            name,
            resolve(self.link),
            self.rtd_type,
            self.ref_resistor,
            self.wires,
            self.filter_hz,
            label=label,
        )


Max31865.config_type = Max31865Config  # the config is declared after the device it builds


__all__ = [
    "FAULT_BITS",
    "R0_OHMS",
    "RTD_A",
    "RTD_B",
    "RTD_C",
    "FilterHz",
    "Max31865",
    "Max31865Config",
    "RtdType",
    "Wires",
    "config",
    "resistance_to_temperature",
    "temperature_to_resistance",
]
