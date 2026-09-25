"""MAX31856: register decode, config-register encoding, and continuous/one-shot reads."""

from __future__ import annotations

import pytest
from flyball_sim.links import FakeSpi

from flyball_chips import max31856


def _encode_cj(temperature: float) -> tuple[int, int]:
    raw = round(temperature / max31856.COLD_JUNCTION_LSB) & 0x3FFF
    shifted = raw << 2
    return (shifted >> 8) & 0xFF, shifted & 0xFF


def _encode_linearized(temperature: float) -> tuple[int, int, int]:
    raw = round(temperature / max31856.LINEARIZED_LSB) & 0x7FFFF
    shifted = raw << 5
    return (shifted >> 16) & 0xFF, (shifted >> 8) & 0xFF, shifted & 0xFF


def _registers(
    temperature: float = 100.0, cold_junction: float = 25.0, status: int = 0
) -> list[int]:
    regs = [0] * 16
    regs[0x0A], regs[0x0B] = _encode_cj(cold_junction)
    regs[0x0C], regs[0x0D], regs[0x0E] = _encode_linearized(temperature)
    regs[0x0F] = status
    return regs


def test_decode_linearized_and_cold_junction():
    thermocouple, cold_junction = max31856.decode(bytes(_registers(150.125, 23.5)))
    assert thermocouple == pytest.approx(150.125)
    assert cold_junction == pytest.approx(23.5)


def test_decode_a_negative_thermocouple_reading():
    thermocouple, _ = max31856.decode(bytes(_registers(-20.25, 23.5)))
    assert thermocouple == pytest.approx(-20.25)


@pytest.mark.parametrize(
    "bit,reason",
    [
        (0x01, "open_circuit"),
        (0x02, "over_under_voltage"),
        (0x04, "low"),
        (0x08, "high"),
        (0x40, "range"),
    ],
)
def test_every_thermocouple_fault_bit_is_invalid(bit, reason):
    thermocouple, cold_junction = max31856.decode(bytes(_registers(status=bit)))
    assert thermocouple.quality == "invalid"
    assert thermocouple.reason == reason
    assert cold_junction == pytest.approx(25.0)


@pytest.mark.parametrize("bit,reason", [(0x10, "low"), (0x20, "high"), (0x80, "range")])
def test_every_cold_junction_fault_bit_is_invalid(bit, reason):
    thermocouple, cold_junction = max31856.decode(bytes(_registers(status=bit)))
    assert thermocouple == pytest.approx(100.0)
    assert cold_junction.quality == "invalid"
    assert cold_junction.reason == reason


def test_cr0_and_cr1_encode_the_config():
    assert max31856.cr0(60, "continuous") == (max31856.OCFAULT_MODE_1 | max31856.CMODE_BIT)
    assert max31856.cr0(50, "one_shot") == (max31856.OCFAULT_MODE_1 | max31856.FILTER_50HZ_BIT)
    assert max31856.cr1("K", 1) == 0x03
    assert max31856.cr1("B", 16) == (0b100 << 4)


def test_build_writes_cr0_and_cr1():
    bus = FakeSpi()
    max31856.Max31856("tc", bus, thermocouple_type="K", averaging=1, filter_hz=60)
    assert bus.sent[0] == [max31856.CR0 | 0x80, max31856.OCFAULT_MODE_1 | max31856.CMODE_BIT]
    assert bus.sent[1] == [max31856.CR1 | 0x80, 0x03]


def test_continuous_conversion_reads_registers_once():
    # a full-duplex transfer clocks back as many bytes as it sends, so the
    # register-read reply needs a leading (ignored) echo byte ahead of the 16 registers
    bus = FakeSpi(replies=[[0, *_registers(100.0, 25.0)]])
    chip = max31856.Max31856("tc", bus, conversion="continuous")
    (sample,) = chip.read(0)
    assert sample.by_name() == {
        "temperature": pytest.approx(100.0),
        "cold_junction": pytest.approx(25.0),
    }
    # two build-time writes, then one register read
    assert bus.sent[2] == [max31856.CR0, *([0] * 16)]


def test_one_shot_conversion_triggers_a_conversion_before_reading():
    bus = FakeSpi(replies=[[0, *_registers(100.0, 25.0)]])
    chip = max31856.Max31856("tc", bus, conversion="one_shot", sleep=False)
    (sample,) = chip.read(0)
    assert sample.by_name()["temperature"] == pytest.approx(100.0)
    # build writes CR0/CR1, then a 1SHOT trigger write, then the register read
    assert bus.sent[2] == [
        max31856.CR0 | 0x80,
        max31856.OCFAULT_MODE_1 | max31856.ONE_SHOT_BIT,
    ]
    assert bus.sent[3] == [max31856.CR0, *([0] * 16)]
