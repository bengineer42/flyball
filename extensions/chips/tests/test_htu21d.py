"""TE Connectivity / Silicon Labs HTU21D, against a scripted `FakeI2c` -- never real hardware."""

import pytest
from flyball.foundation.errors import HardwareError
from flyball_sim.links import FakeI2c

from flyball_chips import htu21d


def test_crc_matches_the_datasheet_examples():
    assert htu21d.crc8(bytes([0x68, 0x3A])) == 0x7C
    assert htu21d.crc8(bytes([0x4E, 0x85])) == 0x6B


def test_temperature_encode_and_decode_round_trip():
    frame = htu21d.encode(24.72, humidity=False)
    assert htu21d.decode_temperature(frame) == pytest.approx(24.72, abs=0.05)


def test_humidity_encode_and_decode_round_trip():
    frame = htu21d.encode(32.3, humidity=True)
    assert htu21d.decode_humidity(frame) == pytest.approx(32.3, abs=0.05)


def test_a_bad_crc_is_a_hardware_error():
    frame = bytearray(htu21d.encode(25.0, humidity=True))
    frame[2] ^= 0x01
    with pytest.raises(HardwareError, match="CRC"):
        htu21d.decode_humidity(bytes(frame))


def test_declares_humidity_and_temperature_on_the_root():
    air = htu21d.Htu21d("air", FakeI2c(), sleep=False)
    assert {p: str(s.access) for p, s in air.signals.items()} == {
        "humidity": "rp",
        "temperature": "rp",
    }
    assert air.signals["humidity"].unit.symbol == "%RH"
    assert air.signals["temperature"].unit.symbol == "°C"
    assert air.nodes == {}


def test_read_commands_then_collects_one_sample():
    bus = FakeI2c(
        replies={
            0x40: [
                list(htu21d.encode(23.0, humidity=False)),
                list(htu21d.encode(48.0, humidity=True)),
            ]
        }
    )
    air = htu21d.Htu21d("air", bus, sleep=False)
    (sample,) = air.read(9)
    assert sample.node is air.root and sample.time_ns == 9
    assert sample.by_name() == {
        "humidity": pytest.approx(48.0, abs=0.05),
        "temperature": pytest.approx(23.0, abs=0.05),
    }
    assert bus.written == [(0x40, None, [0xF3]), (0x40, None, [0xF5])]
    assert air.config.i2c_address == 0x40


def test_a_missing_chip_raises_so_the_device_goes_offline():
    air = htu21d.Htu21d("air", FakeI2c(), sleep=False)
    with pytest.raises(OSError):
        list(air.read(0))
