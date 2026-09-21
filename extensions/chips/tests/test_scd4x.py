"""The SCD40/SCD41 CO2/temperature/humidity driver, against a scripted bus."""

import pytest
from flyball.foundation.errors import HardwareError
from flyball_sim.links import FakeI2c

from flyball_chips import scd4x
from flyball_chips.scd30 import crc8


class TestDecode:
    def test_encode_and_decode_round_trip_three_values(self):
        co2, t, h = scd4x.decode(scd4x.encode(812.0, 21.5, 55.0))
        assert co2 == pytest.approx(812.0, abs=1.0)
        assert t == pytest.approx(21.5, abs=0.01)
        assert h == pytest.approx(55.0, abs=0.01)

    def test_a_bad_crc_is_a_hardware_error(self):
        frame = bytearray(scd4x.encode(800.0, 21.5, 55.0))
        frame[2] ^= 0x01
        with pytest.raises(HardwareError, match="CRC"):
            scd4x.decode(bytes(frame))

    def test_a_short_frame_is_a_hardware_error(self):
        with pytest.raises(HardwareError, match="9"):
            scd4x.decode(b"\x00" * 6)

    def test_humidity_at_full_scale_raw_stays_at_the_bound(self):
        # raw humidity is a uint16, max ~99.998%RH; the clamp guards a decode
        # edge case rather than one reachable through `encode`.
        word = (0xFFFF).to_bytes(2, "big")
        frame = bytearray(scd4x.encode(400.0, 20.0, 99.0))
        frame[6:8] = word
        frame[8] = crc8(word)
        _, _, h = scd4x.decode(bytes(frame))
        assert 0.0 <= h <= 100.0


class TestScd4x:
    def test_declares_co2_temperature_and_humidity_on_the_root(self):
        air = scd4x.Scd4x("air", FakeI2c(), sleep=False)
        assert {p: str(s.access) for p, s in air.signals.items()} == {
            "conditions": "rp",
            "co2": "rp",
            "temperature": "rp",
            "humidity": "rp",
        }
        assert air.signals["co2"].unit.symbol == "ppm"
        assert air.signals["temperature"].unit.symbol == "°C"
        assert air.signals["humidity"].unit.symbol == "%RH"

    def test_read_starts_periodic_measurement_then_collects_one_sample(self):
        bus = FakeI2c(replies={0x62: [list(scd4x.encode(950.0, 21.5, 55.0))]})
        air = scd4x.Scd4x("air", bus, sleep=False)
        (sample,) = air.read(9)
        assert sample.node is air.root and sample.time_ns == 9
        assert sample.by_name() == {
            "co2": pytest.approx(950.0, abs=1.0),
            "temperature": pytest.approx(21.5, abs=0.01),
            "humidity": pytest.approx(55.0, abs=0.01),
        }
        assert bus.written == [
            (0x62, None, [0x21, 0xB1]),
            (0x62, None, [0xEC, 0x05]),
        ]

    def test_read_polls_data_ready_before_reading(self):
        bus = FakeI2c(
            replies={
                0x62: [
                    [0x00, 0x00, crc8(bytes([0x00, 0x00]))],  # not ready
                    [0x00, 0x01, crc8(bytes([0x00, 0x01]))],  # ready (bit 0 set)
                    list(scd4x.encode(700.0, 20.0, 50.0)),
                ]
            }
        )
        air = scd4x.Scd4x("air", bus, sleep=True)
        (sample,) = air.read(0)
        assert sample.by_name()["co2"] == pytest.approx(700.0, abs=1.0)

    def test_a_measurement_that_never_becomes_ready_times_out(self):
        bus = FakeI2c(replies={0x62: [[0x00, 0x00, crc8(bytes([0x00, 0x00]))]]})
        air = scd4x.Scd4x("air", bus, sleep=True)
        air.sensor.timeout_s = 0.1
        with pytest.raises(HardwareError, match="not ready"):
            list(air.read(0))

    def test_single_shot_is_scd41_only(self):
        with pytest.raises(ValueError, match="SCD41"):
            scd4x.Scd4x("air", FakeI2c(), variant="scd40", single_shot=True)

    def test_single_shot_writes_the_single_shot_command_then_reads(self):
        bus = FakeI2c(replies={0x62: [list(scd4x.encode(600.0, 22.0, 40.0))]})
        air = scd4x.Scd4x("air", bus, variant="scd41", single_shot=True, sleep=False)
        (sample,) = air.read(0)
        assert sample.by_name()["co2"] == pytest.approx(600.0, abs=1.0)
        assert bus.written == [(0x62, None, [0x21, 0x9D]), (0x62, None, [0xEC, 0x05])]

    def test_a_missing_chip_raises_so_the_device_goes_offline(self):
        air = scd4x.Scd4x("air", FakeI2c(), sleep=False)
        with pytest.raises(OSError):
            list(air.read(0))

    def test_config_round_trips_the_address_and_variant(self):
        air = scd4x.Scd4x("air", FakeI2c(), address=0x62, variant="scd41", sleep=False)
        assert air.config.address == 0x62 and air.config.variant == "scd41"
