"""The chips with a protocol, against scripted buses."""

import pytest
from flyball.core.errors import HardwareError

from flyball_linux.devices.chips import ads1115, mcp3008, sht4x
from flyball_linux.links.i2c import FakeI2c
from flyball_linux.links.spi import FakeSpi


class TestSht4x:
    def test_crc_matches_the_datasheet_example(self):
        assert sht4x.crc8(b"\xbe\xef") == 0x92

    def test_encode_and_decode_round_trip(self):
        t, h = sht4x.decode(sht4x.encode(21.5, 55.0))
        assert t == pytest.approx(21.5, abs=0.01) and h == pytest.approx(55.0, abs=0.01)

    def test_a_bad_crc_is_a_hardware_error(self):
        frame = bytearray(sht4x.encode(20.0, 50.0))
        frame[2] ^= 0x01
        with pytest.raises(HardwareError, match="CRC"):
            sht4x.decode(bytes(frame))

    def test_reader_commands_then_reads(self, fresh):
        bus = FakeI2c(replies={0x44: [list(sht4x.encode(21.5, 55.0))]})
        reader = sht4x.Sht4xReader(fresh("air"), bus, precision="low", sleep=False)
        (sample,) = reader.read(9)
        values = {m.name: v for m, v in sample.values.items()}
        assert values["temperature"] == pytest.approx(21.5, abs=0.01)
        assert values["humidity"] == pytest.approx(55.0, abs=0.01)
        assert bus.written == [(0x44, None, [0xE0])]
        assert reader.state.humidity == pytest.approx(55.0, abs=0.01)


class TestAds1115:
    def test_config_word(self):
        # channel 0, gain 1 (±4.096 V), single shot, 128 SPS, comparator off
        assert ads1115.config_word(0, 1) == 0xC383

    def test_reader_writes_config_and_reads_conversion(self, fresh):
        bus = FakeI2c(registers={0x48: {0x00: [0x40, 0x00]}})  # 16384 LSB = half scale
        reader = ads1115.Ads1115Reader(
            fresh("adc"),
            bus,
            {"pressure": ads1115.Channel(channel=1, scale=25.0, unit="kPa")},
            gain=1,
            sleep=False,
        )
        (sample,) = reader.read(1)
        assert next(iter(sample.values.values())) == pytest.approx(2.048 * 25.0)
        assert bus.written == [(0x48, 0x01, [0xD3, 0x83])]
        assert reader.state.volts["pressure"] == pytest.approx(2.048)

    def test_unknown_gain_is_refused(self, fresh):
        with pytest.raises(ValueError, match="gain"):
            ads1115.Ads1115Reader(fresh("adc"), FakeI2c(), {}, gain=3)


class TestMcp3008:
    def test_request_and_decode(self):
        assert mcp3008.request(3) == [0x01, 0xB0, 0x00]
        assert mcp3008.decode(b"\x00\x02\xff") == 0x2FF

    def test_reader(self, fresh):
        spi = FakeSpi(lambda sent: [0, 0x03, 0xFF] if sent[1] == 0x80 else [0, 0, 0])
        reader = mcp3008.Mcp3008Reader(
            fresh("pot"),
            spi,
            {"level": mcp3008.Channel(channel=0), "zero": mcp3008.Channel(channel=1)},
        )
        (sample,) = reader.read(1)
        values = {m.name: v for m, v in sample.values.items()}
        assert values["level"] == pytest.approx(3.3) and values["zero"] == 0.0
        assert reader.state.counts == {"level": 1023, "zero": 0}
