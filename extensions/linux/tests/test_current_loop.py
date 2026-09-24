"""4-20mA current-loop scaling and fault detection, against a fake ADS1115 bus."""

import pytest
from flyball.foundation.device import invalid, normalised
from flyball_chips import ads1115
from flyball_sim.links import FakeI2c, FakeI2cConfig

from flyball_linux.devices import current_loop


def _raw_for(volts: float, gain: float = 1) -> list[int]:
    """The two conversion-register bytes an ADS1115 at `gain` would hold for `volts`."""
    _, full_scale = ads1115.FULL_SCALE[gain]
    raw = round(volts / full_scale * 32768.0)
    return list(raw.to_bytes(2, "big", signed=True))


class TestCurrentLoop:
    def test_a_mid_scale_reading_maps_ma_to_engineering_units(self):
        # 250 ohm sense resistor, 12 mA -> 3.0 V, gain 1 (+-4.096 V full scale).
        bus = FakeI2c(registers={0x48: {0x00: _raw_for(3.0)}})
        adc = ads1115.Ads1115(
            "adc",
            bus,
            {"o2": ads1115.Channel(channel=0, unit="mA", scale=1000.0 / 250.0)},
            sleep=False,
        )
        loop = current_loop.CurrentLoop(
            "oxygen",
            adc,
            {
                "o2": current_loop.CurrentLoopChannel(
                    channel=0, unit="%", resistor_ohms=250.0, scale=1.5625, offset=-6.25
                )
            },
        )
        assert loop.signals["o2"].unit.symbol == "%"
        (sample,) = loop.read(1)
        assert sample.by_name() == {"o2": pytest.approx(12.5, abs=0.01)}, (
            "12 mA over a 4-20 mA -> 0-25% span is 12.5%"
        )

    def test_four_and_twenty_ma_hit_the_span_endpoints(self):
        for volts, expected in [(1.0, 0.0), (5.0, 25.0)]:  # 4 mA, 20 mA at 250 ohm
            bus = FakeI2c(registers={0x48: {0x00: _raw_for(volts, gain=2 / 3)}})
            adc = ads1115.Ads1115(
                "adc",
                bus,
                {"o2": ads1115.Channel(channel=0, unit="mA", scale=4.0)},
                gain=2 / 3,
                sleep=False,
            )
            loop = current_loop.CurrentLoop(
                "oxygen",
                adc,
                {
                    "o2": current_loop.CurrentLoopChannel(
                        channel=0, unit="%", resistor_ohms=250.0, scale=1.5625, offset=-6.25
                    )
                },
            )
            (sample,) = loop.read(1)
            assert sample.by_name() == {"o2": pytest.approx(expected, abs=0.01)}

    def test_a_reading_below_the_low_fault_band_is_invalid_low(self):
        # 0.5 V at 250 ohm is 2 mA: well under the NE43-style 3.6 mA floor.
        bus = FakeI2c(registers={0x48: {0x00: _raw_for(0.5)}})
        adc = ads1115.Ads1115(
            "adc", bus, {"o2": ads1115.Channel(channel=0, unit="mA", scale=4.0)}, sleep=False
        )
        loop = current_loop.CurrentLoop(
            "oxygen", adc, {"o2": current_loop.CurrentLoopChannel(channel=0, resistor_ohms=250.0)}
        )
        (sample,) = loop.read(1)
        assert sample.by_name() == {"o2": invalid("ne43_low", side="low")}, "a read, not a raise"

    def test_a_reading_above_the_high_fault_band_is_invalid_high(self):
        # 5.5 V at 250 ohm is 22 mA: over the NE43-style 21 mA ceiling (open/short).
        bus = FakeI2c(registers={0x48: {0x00: _raw_for(5.5, gain=2 / 3)}})
        adc = ads1115.Ads1115(
            "adc",
            bus,
            {"o2": ads1115.Channel(channel=0, unit="mA", scale=4.0)},
            gain=2 / 3,
            sleep=False,
        )
        loop = current_loop.CurrentLoop(
            "oxygen", adc, {"o2": current_loop.CurrentLoopChannel(channel=0, resistor_ohms=250.0)}
        )
        (sample,) = loop.read(1)
        assert sample.by_name() == {"o2": invalid("ne43_high", side="high")}

    @pytest.mark.parametrize(("volts", "side"), [(0.94, "low"), (5.15, "high")])
    def test_a_saturated_current_is_its_value_railed_at_that_end(self, volts, side):
        # 3.76 mA and 20.6 mA at 250 ohm: pinned, not faulted.
        bus = FakeI2c(registers={0x48: {0x00: _raw_for(volts, gain=2 / 3)}})
        adc = ads1115.Ads1115(
            "adc",
            bus,
            {"o2": ads1115.Channel(channel=0, unit="mA", scale=4.0)},
            gain=2 / 3,
            sleep=False,
        )
        loop = current_loop.CurrentLoop(
            "oxygen", adc, {"o2": current_loop.CurrentLoopChannel(channel=0, resistor_ohms=250.0)}
        )
        (sample,) = loop.read(1)
        marked = sample.by_name()["o2"]
        assert marked.side == side and marked.value == pytest.approx(volts * 4.0, abs=0.01)
        (gated,) = normalised(sample).readings()
        assert gated.usable and gated.at_limit == side

    def test_no_channels_is_refused(self):
        adc = ads1115.Ads1115("adc", FakeI2c(), {"a": ads1115.Channel(channel=0)}, sleep=False)
        with pytest.raises(ValueError, match="at least one channel"):
            current_loop.CurrentLoop("oxygen", adc, {})

    def test_config_builds_the_wrapped_adc_with_a_milliamp_channel(self):
        bus_config = FakeI2cConfig.tagged()(registers={0x48: {0x00: _raw_for(3.0)}})
        config = current_loop.CurrentLoopConfig(
            adc=ads1115.Ads1115Config(link=bus_config, channels={}),
            channels={
                "o2": current_loop.CurrentLoopChannel(
                    channel=0, unit="%", resistor_ohms=250.0, scale=1.5625, offset=-6.25
                )
            },
        )
        loop = config.build("oxygen")
        loop.adc.sleep = False
        (sample,) = loop.read(1)
        assert sample.by_name() == {"o2": pytest.approx(12.5, abs=0.01)}
        assert loop.config.channels["o2"].resistor_ohms == 250.0
