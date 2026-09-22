"""Sensirion SHT31, against a scripted `FakeI2c` -- never real hardware."""

import pytest
from flyball.foundation.errors import HardwareError
from flyball_sim.links import FakeI2c

from flyball_chips import sht31


def test_crc_matches_the_datasheet_example():
    assert sht31.crc8(b"\xbe\xef") == 0x92


def test_encode_and_decode_round_trip():
    t, h = sht31.decode(sht31.encode(21.5, 55.0))
    assert t == pytest.approx(21.5, abs=0.01) and h == pytest.approx(55.0, abs=0.01)


def test_a_bad_crc_is_a_hardware_error():
    frame = bytearray(sht31.encode(20.0, 50.0))
    frame[2] ^= 0x01
    with pytest.raises(HardwareError, match="CRC"):
        sht31.decode(bytes(frame))


def test_declares_humidity_and_temperature_on_the_root():
    air = sht31.Sht31("air", FakeI2c(), sleep=False)
    assert {p: str(s.access) for p, s in air.signals.items()} == {
        "conditions": "rp",
        "humidity": "rp",
        "temperature": "rp",
    }
    assert air.signals["humidity"].unit.symbol == "%RH"
    assert air.signals["temperature"].unit.symbol == "°C"
    assert air.nodes == {}


def test_read_commands_then_collects_one_sample():
    bus = FakeI2c(replies={0x44: [list(sht31.encode(21.5, 55.0))]})
    air = sht31.Sht31("air", bus, precision="low", sleep=False)
    (sample,) = air.read(9)
    assert sample.node is air.root and sample.time_ns == 9
    assert sample.by_name() == {
        "humidity": pytest.approx(55.0, abs=0.01),
        "temperature": pytest.approx(21.5, abs=0.01),
    }
    assert bus.written == [(0x44, None, [0x24, 0x16])]
    assert air.config.address == 0x44 and air.config.precision == "low"


def test_a_missing_chip_raises_so_the_device_goes_offline():
    air = sht31.Sht31("air", FakeI2c(), sleep=False)
    with pytest.raises(OSError):
        list(air.read(0))


def test_alternate_address():
    bus = FakeI2c(replies={0x45: [list(sht31.encode(19.0, 40.0))]})
    air = sht31.Sht31("air", bus, address=0x45, sleep=False)
    (sample,) = air.read(0)
    assert sample.by_name()["temperature"] == pytest.approx(19.0, abs=0.01)
    assert bus.written == [(0x45, None, [0x24, 0x00])]
