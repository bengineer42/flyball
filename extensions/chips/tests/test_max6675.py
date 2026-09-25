"""MAX6675: the 16-bit decode, against a fake SPI bus."""

from __future__ import annotations

import pytest
from flyball_sim.links import FakeSpi

from flyball_chips import max6675


def _encode16(raw12: int, open_bit: bool = False) -> list[int]:
    word = (raw12 & 0x0FFF) << 3
    if open_bit:
        word |= max6675.OPEN_BIT
    return list(word.to_bytes(2, "big"))


def test_decode_the_datasheet_style_example():
    # 25.00 degC -> raw12 = 25.00 / 0.25 = 100
    assert max6675.decode(bytes(_encode16(100))) == pytest.approx(25.0)


def test_a_high_reading_near_full_scale():
    # 1023.75 degC -> raw12 = 4095 (12-bit max), unsigned: no negative mode
    assert max6675.decode(bytes(_encode16(4095))) == pytest.approx(1023.75)


def test_open_thermocouple_is_invalid_not_a_raise():
    value = max6675.decode(bytes(_encode16(100, open_bit=True)))
    assert value.quality == "invalid"
    assert value.reason == "open_circuit"


def test_read_yields_one_sample_from_one_transfer():
    bus = FakeSpi(replies=[_encode16(80)])
    chip = max6675.Max6675("tc", bus)
    (sample,) = chip.read(0)
    assert sample.by_name() == {"temperature": pytest.approx(20.0)}
    assert bus.sent == [[0, 0]]


def test_config_round_trips():
    chip = max6675.Max6675("tc", FakeSpi())
    assert chip.config.link == ""
