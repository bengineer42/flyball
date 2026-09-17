"""MCP4725 Fast Mode write encoding, and a full write through `FakeI2c`."""

import pytest

from flyball_linux.devices.chips import mcp4725
from flyball_linux.links.i2c import FakeI2c


class TestEncode:
    def test_zero_code_normal_mode(self):
        assert mcp4725.encode(0x000) == bytes([0x00, 0x00])

    def test_full_scale_code_normal_mode(self):
        assert mcp4725.encode(0xFFF) == bytes([0x0F, 0xFF])

    def test_mid_scale_code_from_the_datasheet_example(self):
        # 0x800 (half of 0xFFF+1): byte 1 top nibble 0000, PD 00, D11-D8 = 1000
        assert mcp4725.encode(0x800) == bytes([0x08, 0x00])

    def test_arbitrary_code(self):
        # 0x123: D11-D8 = 0001, D7-D0 = 0x23
        assert mcp4725.encode(0x123) == bytes([0x01, 0x23])

    def test_power_down_bits_occupy_the_top_nibble_below_the_two_fixed_bits(self):
        assert mcp4725.encode(0x000, power_down=0b01) == bytes([0x10, 0x00])
        assert mcp4725.encode(0x000, power_down=0b10) == bytes([0x20, 0x00])
        assert mcp4725.encode(0x000, power_down=0b11) == bytes([0x30, 0x00])

    def test_rejects_a_code_out_of_range(self):
        with pytest.raises(ValueError, match="code"):
            mcp4725.encode(0x1000)
        with pytest.raises(ValueError, match="code"):
            mcp4725.encode(-1)

    def test_rejects_a_power_down_value_out_of_range(self):
        with pytest.raises(ValueError, match="power_down"):
            mcp4725.encode(0, power_down=4)


class TestMcp4725Output:
    def test_write_clamps_and_returns_the_achieved_fraction(self):
        bus = FakeI2c()
        output = mcp4725.Mcp4725Output(bus, address=0x60)
        assert output.write(-0.5) == pytest.approx(0.0)
        assert output.write(1.5) == pytest.approx(1.0)

    def test_write_sends_one_fast_mode_frame_at_the_address(self):
        bus = FakeI2c()
        output = mcp4725.Mcp4725Output(bus, address=0x60)
        output.write(1.0)
        assert bus.written == [(0x60, None, list(mcp4725.encode(mcp4725.FULL_SCALE)))]


class TestMcp4725Device:
    def test_bare_drive_is_a_zero_to_one_demand(self):
        dac = mcp4725.Mcp4725("dac", FakeI2c())
        signal = dac.signals["drive"]
        assert str(signal.access) == "rpw"
        assert signal.limits == (0.0, 1.0)

    def test_construction_writes_zero_to_the_bus(self):
        bus = FakeI2c()
        mcp4725.Mcp4725("dac", bus, address=0x60)
        assert bus.written == [(0x60, None, list(mcp4725.encode(0)))]

    def test_write_signal_drives_the_bare_fraction(self):
        bus = FakeI2c()
        dac = mcp4725.Mcp4725("dac", bus, address=0x60)
        dac.write_signal(dac.signals["drive"], 0.5)
        code = round(0.5 * mcp4725.FULL_SCALE)
        assert bus.written[-1] == (0x60, None, list(mcp4725.encode(code)))

    def test_unit_and_span_map_engineering_units_onto_the_fraction(self):
        bus = FakeI2c()
        dac = mcp4725.Mcp4725(
            "dac", bus, address=0x60, unit="V", quantity="drive", span=(0.0, 10.0)
        )
        signal = dac.signals["drive"]
        assert signal.unit.symbol == "V"
        assert signal.limits == (0.0, 10.0)
        dac.write_signal(signal, 5.0)
        code = round(0.5 * mcp4725.FULL_SCALE)
        assert bus.written[-1] == (0x60, None, list(mcp4725.encode(code)))

    def test_unit_without_span_is_rejected(self):
        with pytest.raises(ValueError, match="unit.*span"):
            mcp4725.Mcp4725("dac", FakeI2c(), unit="V")

    def test_config_round_trips_address_and_span(self):
        bus = FakeI2c()
        dac = mcp4725.Mcp4725(
            "dac", bus, address=0x61, unit="V", quantity="drive", span=(0.0, 10.0)
        )
        config = dac.config
        assert config.address == 0x61
        assert config.unit == "V"
        assert config.span == (0.0, 10.0)
