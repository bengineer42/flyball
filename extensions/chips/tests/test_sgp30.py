"""SGP30: command/CRC protocol and baseline round trip, against a scripted bus."""

import pytest
from flyball.foundation.errors import HardwareError
from flyball_sim.links import FakeI2c

from flyball_chips import sgp30


class TestProtocol:
    def test_crc_matches_the_sensirion_example(self):
        # Shared Sensirion CRC-8 example: 0xBEEF -> 0x92 (also used by SHT4x).
        assert sgp30.crc8(b"\xbe\xef") == 0x92

    def test_word_with_crc_round_trips(self):
        word = sgp30.word_with_crc(0x1234)
        assert word[:2] == [0x12, 0x34]
        assert sgp30.crc8(bytes(word[:2])) == word[2]

    def test_decode_words_reads_two_crc_checked_words(self):
        frame = bytes(sgp30.word_with_crc(400) + sgp30.word_with_crc(0))
        assert sgp30.decode_words(frame, 2) == [400, 0]

    def test_a_bad_crc_is_a_hardware_error(self):
        frame = bytearray(sgp30.word_with_crc(400) + sgp30.word_with_crc(0))
        frame[2] ^= 0x01
        with pytest.raises(HardwareError, match="CRC"):
            sgp30.decode_words(bytes(frame), 2)

    def test_a_short_frame_is_a_hardware_error(self):
        with pytest.raises(HardwareError, match="6"):
            sgp30.decode_words(b"\x00\x00\x00", 2)


class TestSgp30Sensor:
    def test_init_writes_the_init_command(self):
        bus = FakeI2c()
        sensor = sgp30.Sgp30Sensor(bus, sleep=False)
        sensor.init_air_quality()
        assert bus.written == [(sgp30.SGP30_ADDRESS, None, [0x20, 0x03])]

    def test_measure_writes_command_and_decodes_the_reply(self):
        frame = sgp30.word_with_crc(400) + sgp30.word_with_crc(10)
        bus = FakeI2c(replies={sgp30.SGP30_ADDRESS: [frame]})
        sensor = sgp30.Sgp30Sensor(bus, sleep=False)
        assert sensor.measure() == (400, 10)
        assert bus.written == [(sgp30.SGP30_ADDRESS, None, [0x20, 0x08])]

    def test_get_baseline_decodes_two_words(self):
        frame = sgp30.word_with_crc(0x8973) + sgp30.word_with_crc(0x8AAE)
        bus = FakeI2c(replies={sgp30.SGP30_ADDRESS: [frame]})
        sensor = sgp30.Sgp30Sensor(bus, sleep=False)
        assert sensor.get_baseline() == sgp30.Baseline(co2eq=0x8973, tvoc=0x8AAE)

    def test_set_baseline_writes_both_words_with_crc(self):
        bus = FakeI2c()
        sensor = sgp30.Sgp30Sensor(bus, sleep=False)
        sensor.set_baseline(sgp30.Baseline(co2eq=0x8973, tvoc=0x8AAE))
        (address, register, data) = bus.written[0]
        assert address == sgp30.SGP30_ADDRESS and register is None
        assert data[:2] == [0x20, 0x1E]
        assert data[2:5] == sgp30.word_with_crc(0x8973)
        assert data[5:8] == sgp30.word_with_crc(0x8AAE)

    def test_set_absolute_humidity_encodes_8_8_fixed_point(self):
        bus = FakeI2c()
        sensor = sgp30.Sgp30Sensor(bus, sleep=False)
        sensor.set_absolute_humidity(12.0)
        (_, _, data) = bus.written[0]
        assert data[:2] == [0x20, 0x61]
        assert data[2:4] == [12 * 256 >> 8, (12 * 256) & 0xFF]

    def test_a_missing_chip_raises_so_the_device_goes_offline(self):
        sensor = sgp30.Sgp30Sensor(FakeI2c(), sleep=False)
        with pytest.raises(OSError):
            sensor.measure()


class TestSgp30Device:
    def test_declares_co2eq_and_tvoc_on_the_root(self):
        bus = FakeI2c()
        gas = sgp30.Sgp30("gas", bus, sleep=False)
        assert {p: str(s.access) for p, s in gas.signals.items()} == {
            "conditions": "rp",
            "co2eq": "rp",
            "tvoc": "rp",
        }
        assert gas.signals["co2eq"].unit.symbol == "ppm"
        assert gas.signals["tvoc"].unit.symbol == "ppb"

    def test_construction_initialises_the_algorithm(self):
        bus = FakeI2c()
        sgp30.Sgp30("gas", bus, sleep=False)
        assert bus.written[0] == (sgp30.SGP30_ADDRESS, None, [0x20, 0x03])

    def test_construction_can_restore_a_baseline(self):
        bus = FakeI2c()
        baseline = sgp30.Baseline(co2eq=0x8973, tvoc=0x8AAE)
        sgp30.Sgp30("gas", bus, sleep=False, baseline=baseline)
        assert bus.written[0] == (sgp30.SGP30_ADDRESS, None, [0x20, 0x03])
        assert bus.written[1][2][:2] == [0x20, 0x1E]

    def test_read_collects_one_sample(self):
        frame = sgp30.word_with_crc(400) + sgp30.word_with_crc(10)
        bus = FakeI2c(replies={sgp30.SGP30_ADDRESS: [frame]})
        gas = sgp30.Sgp30("gas", bus, sleep=False)
        (sample,) = gas.read(9)
        assert sample.node is gas.root and sample.time_ns == 9
        assert sample.by_name() == {"co2eq": 400, "tvoc": 10}
        assert gas.config.address == sgp30.SGP30_ADDRESS
