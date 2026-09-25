"""MAX31855: the 32-bit decode -- thermocouple, cold junction, and every fault bit."""

from __future__ import annotations

import pytest
from flyball_sim.links import FakeSpi

from flyball_chips import max31855


def _encode32(raw14: int, raw12: int, fault: int = 0) -> list[int]:
    word = ((raw14 & 0x3FFF) << 18) | ((raw12 & 0xFFF) << 4) | fault
    return list(word.to_bytes(4, "big"))


def test_decode_a_positive_reading_with_cold_junction():
    # 150.25 degC thermocouple (raw14=601), 25.0625 degC cold junction (raw12=401)
    thermocouple, cold_junction = max31855.decode(bytes(_encode32(601, 401)))
    assert thermocouple == pytest.approx(150.25)
    assert cold_junction == pytest.approx(25.0625)


def test_decode_a_negative_thermocouple_reading():
    # -40.00 degC -> raw14 = -160 (two's complement in 14 bits)
    thermocouple, _ = max31855.decode(bytes(_encode32(-160, 401)))
    assert thermocouple == pytest.approx(-40.0)


def test_open_circuit_fault_is_invalid_cold_junction_still_reported():
    thermocouple, cold_junction = max31855.decode(
        bytes(_encode32(601, 401, fault=max31855.FAULT_BIT | max31855.OC_BIT))
    )
    assert thermocouple.quality == "invalid"
    assert thermocouple.reason == "open_circuit"
    assert cold_junction == pytest.approx(25.0625)


def test_multiple_fault_bits_join_the_reason():
    fault = max31855.FAULT_BIT | max31855.OC_BIT | max31855.SCG_BIT
    thermocouple, _ = max31855.decode(bytes(_encode32(0, 0, fault=fault)))
    assert thermocouple.reason == "open_circuit+short_to_gnd"


def test_read_yields_both_signals_from_one_transfer():
    bus = FakeSpi(replies=[_encode32(400, 320)])  # 100.0 degC, 20.0 degC
    chip = max31855.Max31855("tc", bus)
    (sample,) = chip.read(0)
    assert sample.by_name() == {
        "temperature": pytest.approx(100.0),
        "cold_junction": pytest.approx(20.0),
    }
    assert bus.sent == [[0, 0, 0, 0]]
