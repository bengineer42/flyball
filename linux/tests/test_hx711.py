"""HX711: the 24-bit decode and gain/channel pulse-count selection.

What this *can't* meaningfully test: the real chip's bit-banged protocol is
timing-sensitive (PD_SCK high 0.2-50us, low >=0.2us, >60us high = power
down -- see the driver module docstring), and `FakeGpio` has no concept of
a value that changes with each clock edge, so there is no way to fake real
`PD_SCK`/`DOUT` timing behaviour here. `_ScriptedGpio` below stands in only
for the *logic* -- it hands back a pre-programmed bit per rising edge and
counts how many edges it saw -- so these tests confirm the decode and pulse
counts, not that the driver would survive real hardware timing.
"""

from __future__ import annotations

import pytest
from flyball.core.errors import HardwareError

from flyball_linux.devices.chips.hx711 import GAIN_PULSES, Hx711Sensor, decode


class _ScriptedGpio:
    """Feeds a fixed sequence of DOUT bits, one per `PD_SCK` rising edge.

    Not a general fake (see module docstring): it only tracks which line is
    clock vs. data and counts rising edges to index into `bits`.
    """

    def __init__(
        self, data_line: int, clock_line: int, bits: list[bool], ready: bool = True
    ) -> None:
        self.data_line = data_line
        self.clock_line = clock_line
        self.bits = bits
        self.ready = ready
        self.claimed: dict[int, str] = {}
        self.rising_edges = 0

    def claim_output(self, line: int, initial: bool = False) -> None:
        self.claimed[line] = "output"

    def claim_input(self, line: int, pull_up: bool | None = None) -> None:
        self.claimed[line] = "input"

    def set(self, line: int, value: bool) -> None:
        if line == self.clock_line and value:
            self.rising_edges += 1

    def get(self, line: int) -> bool:
        if line != self.data_line:
            raise OSError(f"unexpected line {line}")
        if self.rising_edges == 0:
            return not self.ready
        index = self.rising_edges - 1
        return self.bits[index] if index < len(self.bits) else False


def bits_of(raw24: int, count: int) -> list[bool]:
    """MSB-first bits of a 24-bit field, as the chip would shift them out."""
    return [bool((raw24 >> (23 - i)) & 1) for i in range(count)]


def test_decode_two_s_complement_edges():
    assert decode(0x000000) == 0
    assert decode(0x000001) == 1
    assert decode(0x7FFFFF) == 8_388_607
    assert decode(0x800000) == -8_388_608
    assert decode(0xFFFFFF) == -1


def test_gain_pulses_match_datasheet_table_3():
    # Avia Semiconductor HX711 datasheet, "Serial Interface", Table 3.
    assert GAIN_PULSES == {"a128": 25, "b32": 26, "a64": 27}


@pytest.mark.parametrize(
    ("gain", "raw24", "expected"),
    [
        ("a128", 0x123456, decode(0x123456)),
        ("b32", 0x800001, decode(0x800001)),
        ("a64", 0x7FFFFE, decode(0x7FFFFE)),
    ],
)
def test_read_raw_decodes_all_24_bits_and_clocks_the_right_pulse_count(gain, raw24, expected):
    total_pulses = GAIN_PULSES[gain]
    gpio = _ScriptedGpio(data_line=1, clock_line=0, bits=bits_of(raw24, 24))
    sensor = Hx711Sensor(gpio, clock_line=0, data_line=1, gain=gain)
    assert sensor.read_raw() == expected
    assert gpio.rising_edges == total_pulses


def test_read_raw_refuses_when_dout_is_not_low():
    gpio = _ScriptedGpio(data_line=1, clock_line=0, bits=[], ready=False)
    sensor = Hx711Sensor(gpio, clock_line=0, data_line=1)
    with pytest.raises(HardwareError):
        sensor.read_raw()


def test_ready_reflects_dout_before_any_clocking():
    gpio = _ScriptedGpio(data_line=1, clock_line=0, bits=[], ready=True)
    sensor = Hx711Sensor(gpio, clock_line=0, data_line=1)
    assert sensor.ready() is True
    gpio.ready = False
    gpio2 = _ScriptedGpio(data_line=1, clock_line=0, bits=[], ready=False)
    sensor2 = Hx711Sensor(gpio2, clock_line=0, data_line=1)
    assert sensor2.ready() is False
