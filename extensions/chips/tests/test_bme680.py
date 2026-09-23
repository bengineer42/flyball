"""BME680: calibration parsing, T/H/P/gas compensation and the heater sequence."""

import pytest
from flyball.foundation.errors import HardwareError
from flyball_sim.links import FakeI2c

from flyball_chips import bme680

ADDRESS = bme680.BME680_ADDRESS


def _coeff_bytes() -> tuple[bytes, bytes]:
    """25 bytes at 0x89 and 16 bytes at 0xE1, built to round-trip a chosen calibration."""
    coeff1 = bytearray(25)
    coeff1[1:3] = (26435).to_bytes(2, "little", signed=True)  # par_t2
    coeff1[3] = (6) & 0xFF  # par_t3
    coeff1[4:6] = (36477).to_bytes(2, "little", signed=False)  # par_p1
    coeff1[6:8] = (-10685 & 0xFFFF).to_bytes(2, "little")  # par_p2
    coeff1[8] = (-4) & 0xFF  # par_p3
    coeff1[10:12] = (2855).to_bytes(2, "little", signed=True)  # par_p4
    coeff1[12:14] = (140).to_bytes(2, "little", signed=True)  # par_p5
    coeff1[14] = (30) & 0xFF  # par_p7
    coeff1[15] = (-7) & 0xFF  # par_p6
    coeff1[18:20] = (-14600 & 0xFFFF).to_bytes(2, "little")  # par_p8
    coeff1[20:22] = (6000).to_bytes(2, "little", signed=True)  # par_p9
    coeff1[22] = 30  # par_p10
    coeff1[24] = (10 << 4) & 0xF0  # range_sw_err nibble = 10 (signed -> negative once shifted)

    coeff2 = bytearray(16)
    coeff2[8:10] = (27504).to_bytes(2, "little", signed=False)  # par_t1
    # par_h1 (12 bit) = 600, par_h2 (12 bit) = 1000, packed like BME280's dig_h4/h5.
    h1, h2 = 600, 1000
    coeff2[0] = (h2 >> 4) & 0xFF
    coeff2[1] = (h1 & 0x0F) | ((h2 & 0x0F) << 4)
    coeff2[2] = (h1 >> 4) & 0xFF
    coeff2[3] = 30  # par_h3
    coeff2[4] = (-6) & 0xFF  # par_h4
    coeff2[5] = 20  # par_h5
    coeff2[6] = 120  # par_h6
    coeff2[7] = (-100) & 0xFF  # par_h7
    coeff2[10:12] = (-30000 & 0xFFFF).to_bytes(2, "little")  # par_gh2
    coeff2[12] = (-20) & 0xFF  # par_gh1
    coeff2[13] = 18  # par_gh3
    coeff2[14] = (1 << 4) & 0x30  # res_heat_range = 1
    coeff2[15] = 40  # res_heat_val
    return bytes(coeff1), bytes(coeff2)


COEFF1, COEFF2 = _coeff_bytes()


def _bus(address: int = ADDRESS) -> FakeI2c:
    return FakeI2c(
        registers={address: {bme680._COEFF1: list(COEFF1), bme680._COEFF2: list(COEFF2)}}
    )


class TestParseCalibration:
    def test_round_trips_the_packed_and_signed_words(self):
        cal = bme680.parse_calibration(COEFF1, COEFF2)
        assert cal.par_t1 == 27504
        assert cal.par_t2 == 26435
        assert cal.par_h1 == 600
        assert cal.par_h2 == 1000
        assert cal.par_h7 == -100

    def test_wrong_length_blocks_are_refused(self):
        with pytest.raises(ValueError, match="25"):
            bme680.parse_calibration(COEFF1[:10], COEFF2)
        with pytest.raises(ValueError, match="16"):
            bme680.parse_calibration(COEFF1, COEFF2[:5])


class TestCompensate:
    def test_temperature_pressure_humidity_are_finite_and_in_band(self):
        cal = bme680.parse_calibration(COEFF1, COEFF2)
        temperature, t_fine = bme680.compensate_temperature(519888, cal)
        assert -40.0 < temperature < 85.0
        pressure = bme680.compensate_pressure(415148, t_fine, cal)
        assert 30000.0 < pressure < 110000.0
        humidity = bme680.compensate_humidity(30000, t_fine, cal)
        assert 0.0 <= humidity <= 100.0

    def test_humidity_is_clamped_to_0_100(self):
        cal = bme680.parse_calibration(COEFF1, COEFF2)
        _, t_fine = bme680.compensate_temperature(519888, cal)
        assert bme680.compensate_humidity(0, t_fine, cal) >= 0.0
        assert bme680.compensate_humidity(65535, t_fine, cal) <= 100.0

    def test_gas_resistance_is_positive(self):
        cal = bme680.parse_calibration(COEFF1, COEFF2)
        for gas_range in range(16):
            resistance = bme680.compensate_gas_resistance(30000, gas_range, cal)
            assert resistance > 0.0


class TestHeaterProfile:
    def test_calc_res_heat_is_a_single_byte(self):
        cal = bme680.parse_calibration(COEFF1, COEFF2)
        res_heat = bme680.calc_res_heat(320, 25.0, cal)
        assert 0 <= res_heat <= 255

    def test_calc_res_heat_clamps_the_target_to_400c(self):
        cal = bme680.parse_calibration(COEFF1, COEFF2)
        assert bme680.calc_res_heat(500, 25.0, cal) == bme680.calc_res_heat(400, 25.0, cal)

    def test_calc_gas_wait_short_durations_are_verbatim(self):
        assert bme680.calc_gas_wait(0) == 0
        assert bme680.calc_gas_wait(63) == 63

    def test_calc_gas_wait_applies_the_multiplier_past_63ms(self):
        assert bme680.calc_gas_wait(150) == 101  # 150 // 4 = 37, factor 1: 37 + 64
        assert bme680.calc_gas_wait(0xFC0) == 0xFF
        assert bme680.calc_gas_wait(10_000) == 0xFF


class TestBme680Sensor:
    def test_reads_both_calibration_blocks_at_construction(self):
        sensor = bme680.Bme680Sensor(_bus(), ADDRESS, sleep=False)
        assert sensor.cal.par_t1 == 27504

    def test_read_loads_the_heater_profile_then_triggers_and_reads(self):
        bus = _bus()
        adc_p, adc_t, adc_h, gas_adc = 415148, 519888, 30000, 20000
        gas_range = 5
        gas_lsb = (
            (gas_adc & 0b11) << 6 | gas_range | bme680._HEAT_STAB_MASK | bme680._GAS_VALID_MASK
        )
        field = [
            0x80,  # status: new_data
            (adc_p >> 12) & 0xFF,
            (adc_p >> 4) & 0xFF,
            (adc_p << 4) & 0xF0,
            (adc_t >> 12) & 0xFF,
            (adc_t >> 4) & 0xFF,
            (adc_t << 4) & 0xF0,
            (adc_h >> 8) & 0xFF,
            adc_h & 0xFF,
        ]
        bus.registers[ADDRESS][bme680._FIELD0] = field
        bus.registers[ADDRESS][bme680._FIELD0 + 9] = [(gas_adc >> 2) & 0xFF, gas_lsb]
        sensor = bme680.Bme680Sensor(bus, ADDRESS, sleep=False)
        temperature, pressure, humidity, gas_resistance = sensor.read()
        assert -40.0 < temperature < 85.0
        assert 30000.0 < pressure < 110000.0
        assert 0.0 <= humidity <= 100.0
        assert gas_resistance > 0.0
        assert (ADDRESS, bme680.CTRL_GAS_1, [bme680._RUN_GAS]) in bus.written
        assert any(reg == bme680.RES_HEAT_0 for _, reg, _ in bus.written)
        assert any(reg == bme680.GAS_WAIT_0 for _, reg, _ in bus.written)

    def test_a_heater_not_stable_is_a_hardware_error(self):
        bus = _bus()
        field = [0x80] + [0] * 8
        bus.registers[ADDRESS][bme680._FIELD0] = field
        # heat_stab bit clear, gas_valid set.
        bus.registers[ADDRESS][bme680._FIELD0 + 9] = [0, bme680._GAS_VALID_MASK]
        sensor = bme680.Bme680Sensor(bus, ADDRESS, sleep=False)
        with pytest.raises(HardwareError, match="not stable"):
            sensor.read()

    def test_an_invalid_gas_reading_is_a_hardware_error(self):
        bus = _bus()
        field = [0x80] + [0] * 8
        bus.registers[ADDRESS][bme680._FIELD0] = field
        # heat_stab set, gas_valid clear.
        bus.registers[ADDRESS][bme680._FIELD0 + 9] = [0, bme680._HEAT_STAB_MASK]
        sensor = bme680.Bme680Sensor(bus, ADDRESS, sleep=False)
        with pytest.raises(HardwareError, match="not valid"):
            sensor.read()


class TestBme680Device:
    def test_declares_all_four_signals(self):
        chip = bme680.Bme680("gas", _bus(), sleep=False)
        assert set(chip.signals) == {
            "conditions",
            "temperature",
            "humidity",
            "pressure",
            "gas_resistance",
        }
        assert chip.signals["gas_resistance"].unit.symbol == "Ω"

    def test_read_yields_one_sample(self):
        bus = _bus()
        adc_p, adc_t, adc_h, gas_adc = 415148, 519888, 30000, 20000
        gas_range = 5
        gas_lsb = (
            (gas_adc & 0b11) << 6 | gas_range | bme680._HEAT_STAB_MASK | bme680._GAS_VALID_MASK
        )
        field = [
            0x80,
            (adc_p >> 12) & 0xFF,
            (adc_p >> 4) & 0xFF,
            (adc_p << 4) & 0xF0,
            (adc_t >> 12) & 0xFF,
            (adc_t >> 4) & 0xFF,
            (adc_t << 4) & 0xF0,
            (adc_h >> 8) & 0xFF,
            adc_h & 0xFF,
        ]
        bus.registers[ADDRESS][bme680._FIELD0] = field
        bus.registers[ADDRESS][bme680._FIELD0 + 9] = [(gas_adc >> 2) & 0xFF, gas_lsb]
        chip = bme680.Bme680("gas", bus, sleep=False)
        (sample,) = chip.read(4)
        assert sample.node is chip.root and sample.time_ns == 4
        by_name = sample.by_name()
        assert set(by_name) == {"temperature", "humidity", "pressure", "gas_resistance"}

    def test_config_round_trips_heater_profile(self):
        chip = bme680.Bme680("gas", _bus(), gas_heater_c=350, gas_wait_ms=200, sleep=False)
        cfg = chip.config
        assert cfg.gas_heater_c == 350
        assert cfg.gas_wait_ms == 200

    def test_a_missing_chip_raises_at_construction(self):
        with pytest.raises(OSError):
            bme680.Bme680("gas", FakeI2c(), sleep=False)


def test_the_driver_is_disabled_until_its_register_offsets_are_fixed():
    """The offsets are wrong (see the module docstring), so building must refuse.

    Deliberately not a skip or an xfail: a rig file naming `bme680` should get an
    explanation, not a device that returns confident nonsense.
    """
    config = bme680.Bme680Config(link="bus", address=0x77)
    with pytest.raises(NotImplementedError, match="register offsets are wrong"):
        config.build("gas")
