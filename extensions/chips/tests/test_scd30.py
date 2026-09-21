"""The SCD30 CO2/temperature/humidity driver, against a scripted bus."""

import pytest
from flyball.foundation.errors import HardwareError
from flyball_sim.links import FakeI2c

from flyball_chips import scd30


class TestCrc:
    def test_crc_matches_the_datasheet_example(self):
        assert scd30.crc8(b"\xbe\xef") == 0x92

    def test_a_bad_crc_is_a_hardware_error(self):
        frame = bytearray(scd30.encode(800.0, 21.5, 55.0))
        frame[2] ^= 0x01
        with pytest.raises(HardwareError, match="CRC"):
            scd30.decode(bytes(frame))

    def test_a_short_frame_is_a_hardware_error(self):
        with pytest.raises(HardwareError, match="18"):
            scd30.decode(b"\x00" * 12)


class TestDecode:
    def test_encode_and_decode_round_trip_three_values(self):
        co2, t, h = scd30.decode(scd30.encode(812.3, 21.5, 55.0))
        assert co2 == pytest.approx(812.3, abs=0.1)
        assert t == pytest.approx(21.5, abs=0.01)
        assert h == pytest.approx(55.0, abs=0.01)

    def test_humidity_is_clamped_to_range(self):
        _, _, h = scd30.decode(scd30.encode(400.0, 20.0, 150.0))
        assert h == 100.0


class TestScd30:
    def test_declares_co2_temperature_and_humidity_on_the_root(self):
        air = scd30.Scd30("air", FakeI2c(), sleep=False)
        assert {p: str(s.access) for p, s in air.signals.items()} == {
            "conditions": "rp",
            "co2": "rp",
            "temperature": "rp",
            "humidity": "rp",
        }
        assert air.signals["co2"].unit.symbol == "ppm"
        assert air.signals["temperature"].unit.symbol == "°C"
        assert air.signals["humidity"].unit.symbol == "%RH"

    def test_read_starts_continuous_measurement_then_collects_one_sample(self):
        bus = FakeI2c(replies={0x61: [list(scd30.encode(950.0, 21.5, 55.0))]})
        air = scd30.Scd30("air", bus, sleep=False)
        (sample,) = air.read(9)
        assert sample.node is air.root and sample.time_ns == 9
        assert sample.by_name() == {
            "co2": pytest.approx(950.0, abs=0.1),
            "temperature": pytest.approx(21.5, abs=0.01),
            "humidity": pytest.approx(55.0, abs=0.01),
        }
        # the constructor starts continuous measurement (pressure compensation off);
        # the read then asks for the measurement directly, skipping the data-ready poll
        assert bus.written == [
            (0x61, None, scd30.command(scd30.CMD_START_CONTINUOUS_MEASUREMENT, 0)),
            (0x61, None, scd30.command(scd30.CMD_READ_MEASUREMENT)),
        ]

    def test_read_polls_data_ready_before_reading(self):
        bus = FakeI2c(
            replies={
                0x61: [
                    [0x00, 0x00, scd30.crc8(bytes([0x00, 0x00]))],  # not ready
                    [0x00, 0x01, scd30.crc8(bytes([0x00, 0x01]))],  # ready
                    list(scd30.encode(700.0, 20.0, 50.0)),
                ]
            }
        )
        air = scd30.Scd30("air", bus, sleep=True)
        (sample,) = air.read(0)
        assert sample.by_name()["co2"] == pytest.approx(700.0, abs=0.1)

    def test_a_measurement_that_never_becomes_ready_times_out(self):
        bus = FakeI2c(replies={0x61: [[0x00, 0x00, scd30.crc8(bytes([0x00, 0x00]))]]})
        air = scd30.Scd30("air", bus, sleep=True)
        air.sensor.timeout_s = 0.1
        with pytest.raises(HardwareError, match="not ready"):
            list(air.read(0))

    def test_a_missing_chip_raises_so_the_device_goes_offline(self):
        air = scd30.Scd30("air", FakeI2c(), sleep=False)
        with pytest.raises(OSError):
            list(air.read(0))

    def test_config_round_trips_the_address(self):
        air = scd30.Scd30("air", FakeI2c(), address=0x61, sleep=False)
        assert air.config.address == 0x61
