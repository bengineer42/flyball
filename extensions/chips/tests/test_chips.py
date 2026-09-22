"""The chips with a protocol, against scripted buses."""

import pytest
from flyball.foundation.device import Access
from flyball.foundation.errors import HardwareError
from flyball_sim.links import FakeI2c, FakeSpi

from flyball_chips import ads1115, mcp3008, sht4x

NS = 1_000_000_000


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

    def test_declares_humidity_and_temperature_on_the_root(self):
        air = sht4x.Sht4x("air", FakeI2c(), sleep=False)
        assert {p: str(s.access) for p, s in air.signals.items()} == {
            "conditions": "rp",
            "humidity": "rp",
            "temperature": "rp",
        }
        assert air.signals["humidity"].unit.symbol == "%RH"
        assert air.signals["temperature"].unit.symbol == "°C"
        assert air.nodes == {}

    def test_read_commands_then_collects_one_sample(self):
        bus = FakeI2c(replies={0x44: [list(sht4x.encode(21.5, 55.0))]})
        air = sht4x.Sht4x("air", bus, precision="low", sleep=False)
        (sample,) = air.read(9)
        assert sample.node is air.root and sample.time_ns == 9
        assert sample.by_name() == {
            "humidity": pytest.approx(55.0, abs=0.01),
            "temperature": pytest.approx(21.5, abs=0.01),
        }
        assert bus.written == [(0x44, None, [0xE0])]
        assert air.config.address == 0x44 and air.config.precision == "low"

    def test_a_missing_chip_raises_so_the_device_goes_offline(self):
        air = sht4x.Sht4x("air", FakeI2c(), sleep=False)
        with pytest.raises(OSError):
            list(air.read(0))


class TestSht4xSet:
    @staticmethod
    def _bus():
        return FakeI2c(
            replies={
                0x44: [list(sht4x.encode(21.0, 50.0))],
                0x45: [list(sht4x.encode(22.0, 5.0))],
                0x46: [list(sht4x.encode(23.0, 95.0))],
            }
        )

    def test_one_atomic_namespace_per_sensor(self):
        hum = sht4x.Sht4xSet("hum", self._bus(), {"chamber": 0x44, "dry": 0x45, "wet": 0x46})
        assert list(hum.nodes) == ["chamber", "dry", "wet"]
        assert all(node.atomic for node in hum.nodes.values())
        assert hum.signals["dry.humidity"].address == "hum.dry.humidity"
        assert hum.signals["dry.humidity"].access is Access.RP

    def test_the_poll_reads_every_sensor_in_its_own_transaction(self):
        bus = self._bus()
        hum = sht4x.Sht4xSet("hum", bus, {"chamber": 0x44, "dry": 0x45}, sleep=False)
        samples = list(hum.read(5))
        assert [s.node.address for s in samples] == ["hum.chamber", "hum.dry"]
        assert samples[1].by_name() == {
            "humidity": pytest.approx(5.0, abs=0.01),
            "temperature": pytest.approx(22.0, abs=0.01),
        }
        assert bus.written == [(0x44, None, [0xFD]), (0x45, None, [0xFD])]

    def test_a_namespace_is_read_on_its_own_poll_s(self):
        hum = sht4x.Sht4xSet("hum", self._bus(), {"chamber": 0x44, "dry": 0x45}, sleep=False)
        hum.poll_s = 1.0
        hum.nodes["dry"].override(poll_s=5.0)
        assert [s.node.name for s in hum.read(0)] == ["chamber", "dry"]
        assert [s.node.name for s in hum.read(1 * NS)] == ["chamber"], "dry is not due at 1 s"
        assert [s.node.name for s in hum.read(5 * NS)] == ["chamber", "dry"]

    def test_a_node_asked_for_is_read_whether_due_or_not(self):
        hum = sht4x.Sht4xSet("hum", self._bus(), {"chamber": 0x44, "dry": 0x45}, sleep=False)
        hum.nodes["dry"].override(poll_s=5.0)
        list(hum.read(0))
        (sample,) = hum.read(1 * NS, hum.nodes["dry"])
        assert sample.node is hum.nodes["dry"]

    def test_no_sensors_is_refused(self):
        with pytest.raises(ValueError, match="at least one sensor"):
            sht4x.Sht4xSet("hum", FakeI2c(), {})

    def test_config_round_trips_the_addresses(self):
        hum = sht4x.Sht4xSet("hum", FakeI2c(), {"dry": 0x45}, precision="medium")
        assert hum.config.sensors == {"dry": sht4x.SensorEntry(address=0x45)}
        assert hum.config.precision == "medium"


class TestAds1115:
    def test_config_word(self):
        # channel 0, gain 1 (±4.096 V), single shot, 128 SPS, comparator off
        assert ads1115.config_word(0, 1) == 0xC383

    def test_read_writes_config_and_reads_conversion(self):
        bus = FakeI2c(registers={0x48: {0x00: [0x40, 0x00]}})  # 16384 LSB = half scale
        adc = ads1115.Ads1115(
            "adc",
            bus,
            {"pressure": ads1115.Channel(channel=1, scale=25.0, unit="kPa")},
            gain=1,
            sleep=False,
        )
        assert adc.signals["pressure"].unit.symbol == "kPa"
        assert adc.signals["pressure"].quantity.name == "pressure"
        (sample,) = adc.read(1)
        assert sample.by_name() == {"pressure": pytest.approx(2.048 * 25.0)}
        assert bus.written == [(0x48, 0x01, [0xD3, 0x83])]

    def test_only_due_signals_are_read(self):
        bus = FakeI2c(registers={0x48: {0x00: [0x40, 0x00]}})
        adc = ads1115.Ads1115(
            "adc",
            bus,
            {"a": ads1115.Channel(channel=0), "b": ads1115.Channel(channel=1)},
            sleep=False,
        )
        adc.poll_s = 1.0
        adc.signals["b"].override(poll_s=10.0)
        assert [s.by_name() for s in adc.read(0)] == [{"a": 2.048, "b": 2.048}]
        assert [s.by_name() for s in adc.read(1 * NS)] == [{"a": 2.048}], "b is not due at 1 s"
        assert list(adc.read(1 * NS)) == [], "nothing due: nothing yielded"
        assert [s.by_name() for s in adc.read(2 * NS, adc.root)] == [{"a": 2.048, "b": 2.048}], (
            "a read asked for takes everything under the node"
        )

    def test_unknown_gain_and_no_channels_are_refused(self):
        with pytest.raises(ValueError, match="gain"):
            ads1115.Ads1115("adc", FakeI2c(), {"a": ads1115.Channel(channel=0)}, gain=3)
        with pytest.raises(ValueError, match="at least one channel"):
            ads1115.Ads1115("adc", FakeI2c(), {})


class TestMcp3008:
    def test_request_and_decode(self):
        assert mcp3008.request(3) == [0x01, 0xB0, 0x00]
        assert mcp3008.decode(b"\x00\x02\xff") == 0x2FF

    def test_read(self):
        spi = FakeSpi(lambda sent: [0, 0x03, 0xFF] if sent[1] == 0x80 else [0, 0, 0])
        pot = mcp3008.Mcp3008(
            "pot", spi, {"level": mcp3008.Channel(channel=0), "zero": mcp3008.Channel(channel=1)}
        )
        (sample,) = pot.read(1)
        assert sample.by_name() == {"level": pytest.approx(3.3), "zero": 0.0}
        assert pot.config.vref == 3.3
