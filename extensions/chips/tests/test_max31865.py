"""MAX31865: config-register encoding, the Callendar-Van Dusen round trip, faults and reads."""

from __future__ import annotations

import pytest
from flyball_sim.links import FakeSpi

from flyball_chips import max31865


def test_config_register_bits():
    assert max31865.config(2, 60) == (max31865.VBIAS_BIT | max31865.AUTO_CONVERT_BIT)
    assert max31865.config(3, 50) == (
        max31865.VBIAS_BIT
        | max31865.AUTO_CONVERT_BIT
        | max31865.THREE_WIRE_BIT
        | max31865.FILTER_50HZ_BIT
    )


@pytest.mark.parametrize("temperature", [0.0, 100.0, -50.0])
def test_cvd_round_trip_pt100(temperature):
    r0 = max31865.R0_OHMS["pt100"]
    resistance = max31865.temperature_to_resistance(temperature, r0)
    assert max31865.resistance_to_temperature(resistance, r0) == pytest.approx(
        temperature, abs=1e-6
    )


def test_pt100_known_table_values():
    # IEC 60751: 0 degC = 100.00 ohm, 100 degC ~= 138.51 ohm, -50 degC ~= 80.31 ohm
    r0 = max31865.R0_OHMS["pt100"]
    assert max31865.temperature_to_resistance(0.0, r0) == pytest.approx(100.0, abs=0.01)
    assert max31865.temperature_to_resistance(100.0, r0) == pytest.approx(138.51, abs=0.01)
    assert max31865.temperature_to_resistance(-50.0, r0) == pytest.approx(80.31, abs=0.01)


def test_read_decodes_resistance_and_temperature():
    r0 = max31865.R0_OHMS["pt100"]
    resistance = max31865.temperature_to_resistance(50.0, r0)
    code = round(resistance / 430.0 * 32768.0)
    raw = code << 1
    bus = FakeSpi(replies=[[0, *raw.to_bytes(2, "big")]])
    rtd = max31865.Max31865("rtd", bus, rtd_type="pt100", ref_resistor=430.0)
    (sample,) = rtd.read(0)
    values = sample.by_name()
    # the 15-bit ADC code quantises the resistance, so both come back close but not exact
    assert values["resistance"] == pytest.approx(resistance, abs=0.02)
    assert values["temperature"] == pytest.approx(50.0, abs=0.05)


@pytest.mark.parametrize(
    "bit,reason",
    [
        (0x80, "high_threshold"),
        (0x40, "low_threshold"),
        (0x20, "refin_low"),
        (0x10, "refin_high"),
        (0x08, "rtdin_low"),
        (0x04, "over_under_voltage"),
    ],
)
def test_every_fault_bit_is_invalid_and_clears_the_latch(bit, reason):
    # replies, in transfer order: the build-time config write (ignored, 2 bytes),
    # the RTD MSB/LSB read (3 bytes sent, D0 set: a fault), then the fault status
    # register read (2 bytes sent) -- each reply needs its own leading echo byte
    bus = FakeSpi(replies=[[0, 0], [0, 0x00, 0x01], [0, bit]])
    rtd = max31865.Max31865("rtd", bus)
    (sample,) = rtd.read(0)
    values = sample.by_name()
    assert values["resistance"].quality == "invalid"
    assert values["resistance"].reason == reason
    assert values["temperature"].reason == reason
    # the last transfer clears the fault latch (config | FAULT_CLEAR_BIT)
    assert bus.sent[-1] == [max31865.CONFIG | 0x80, rtd._config | max31865.FAULT_CLEAR_BIT]


def test_config_round_trips():
    rtd = max31865.Max31865("rtd", FakeSpi(), rtd_type="pt1000", ref_resistor=4300.0, wires=3)
    cfg = rtd.config
    assert cfg.rtd_type == "pt1000"
    assert cfg.ref_resistor == 4300.0
    assert cfg.wires == 3
