"""SGP40: humidity/temperature compensated raw-VOC reads, against a scripted bus."""

import pytest
from flyball.core.errors import HardwareError

from flyball_linux.devices.chips import sgp40
from flyball_linux.links.i2c import FakeI2c


class TestProtocol:
    def test_crc_matches_the_sensirion_example(self):
        assert sgp40.crc8(b"\xbe\xef") == 0x92

    def test_humidity_and_temperature_ticks_at_the_datasheet_defaults(self):
        assert sgp40.humidity_ticks(50.0) == pytest.approx(sgp40.DEFAULT_HUMIDITY_TICKS, abs=1)
        assert sgp40.temperature_ticks(25.0) == pytest.approx(
            sgp40.DEFAULT_TEMPERATURE_TICKS, abs=1
        )

    def test_decode_checks_the_crc(self):
        frame = bytes(sgp40.word_with_crc(0x1234))
        assert sgp40.decode(frame) == 0x1234

    def test_a_bad_crc_is_a_hardware_error(self):
        frame = bytearray(sgp40.word_with_crc(0x1234))
        frame[2] ^= 0x01
        with pytest.raises(HardwareError, match="CRC"):
            sgp40.decode(bytes(frame))

    def test_a_short_frame_is_a_hardware_error(self):
        with pytest.raises(HardwareError, match="3"):
            sgp40.decode(b"\x00\x00")


class TestSgp40Sensor:
    def test_measure_with_no_compensation_uses_the_defaults(self):
        frame = sgp40.word_with_crc(0x5678)
        bus = FakeI2c(replies={sgp40.SGP40_ADDRESS: [frame]})
        sensor = sgp40.Sgp40Sensor(bus, sleep=False)
        assert sensor.measure() == 0x5678
        (address, register, data) = bus.written[0]
        assert address == sgp40.SGP40_ADDRESS and register is None
        assert data[:2] == [0x26, 0x0F]
        assert data[2:5] == sgp40.word_with_crc(sgp40.DEFAULT_HUMIDITY_TICKS)
        assert data[5:8] == sgp40.word_with_crc(sgp40.DEFAULT_TEMPERATURE_TICKS)

    def test_measure_encodes_given_humidity_and_temperature(self):
        frame = sgp40.word_with_crc(0x1111)
        bus = FakeI2c(replies={sgp40.SGP40_ADDRESS: [frame]})
        sensor = sgp40.Sgp40Sensor(bus, sleep=False)
        sensor.measure(humidity_percent_rh=55.0, temperature_c=21.5)
        (_, _, data) = bus.written[0]
        assert data[2:5] == sgp40.word_with_crc(sgp40.humidity_ticks(55.0))
        assert data[5:8] == sgp40.word_with_crc(sgp40.temperature_ticks(21.5))

    def test_heater_off_writes_the_command(self):
        bus = FakeI2c()
        sgp40.Sgp40Sensor(bus, sleep=False).heater_off()
        assert bus.written == [(sgp40.SGP40_ADDRESS, None, [0x36, 0x15])]

    def test_a_missing_chip_raises_so_the_device_goes_offline(self):
        sensor = sgp40.Sgp40Sensor(FakeI2c(), sleep=False)
        with pytest.raises(OSError):
            sensor.measure()


class TestSgp40Device:
    def test_declares_voc_raw_on_the_root(self):
        bus = FakeI2c()
        gas = sgp40.Sgp40("gas", bus, sleep=False)
        assert {p: str(s.access) for p, s in gas.signals.items()} == {
            "conditions": "rp",
            "voc_raw": "rp",
        }

    def test_read_passes_compensation_through_to_the_measure_command(self):
        frame = sgp40.word_with_crc(12345)
        bus = FakeI2c(replies={sgp40.SGP40_ADDRESS: [frame]})
        gas = sgp40.Sgp40("gas", bus, sleep=False)
        (sample,) = gas.read(9, humidity_percent_rh=60.0, temperature_c=18.0)
        assert sample.node is gas.root and sample.time_ns == 9
        assert sample.by_name() == {"voc_raw": 12345}
        (_, _, data) = bus.written[0]
        assert data[2:5] == sgp40.word_with_crc(sgp40.humidity_ticks(60.0))
        assert data[5:8] == sgp40.word_with_crc(sgp40.temperature_ticks(18.0))
        assert gas.config.address == sgp40.SGP40_ADDRESS
