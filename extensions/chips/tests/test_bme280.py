"""BME280/BMP280: calibration parsing and Bosch compensation, against a scripted bus."""

import pytest
from flyball_sim.links import FakeI2c

from flyball_chips import bme280

ADDRESS = bme280.BME280_ADDRESS


def _calibration_bytes() -> tuple[bytes, bytes, bytes]:
    """A plausible calibration block, with negative and boundary trim values exercised."""
    dig_t1 = 27504
    dig_t2 = 26435
    dig_t3 = -1000
    dig_p1 = 36477
    dig_p2 = -10685
    dig_p3 = 3024
    dig_p4 = 2855
    dig_p5 = 140
    dig_p6 = -7
    dig_p7 = 15500
    dig_p8 = -14600
    dig_p9 = 6000
    t_p = (
        dig_t1.to_bytes(2, "little", signed=False)
        + dig_t2.to_bytes(2, "little", signed=True)
        + dig_t3.to_bytes(2, "little", signed=True)
        + dig_p1.to_bytes(2, "little", signed=False)
        + dig_p2.to_bytes(2, "little", signed=True)
        + dig_p3.to_bytes(2, "little", signed=True)
        + dig_p4.to_bytes(2, "little", signed=True)
        + dig_p5.to_bytes(2, "little", signed=True)
        + dig_p6.to_bytes(2, "little", signed=True)
        + dig_p7.to_bytes(2, "little", signed=True)
        + dig_p8.to_bytes(2, "little", signed=True)
        + dig_p9.to_bytes(2, "little", signed=True)
    )
    dig_h1 = 75
    dig_h2 = -170
    dig_h3 = 0
    dig_h4 = -204  # -0xCC: exercises the 12-bit sign-extended pack
    dig_h5 = 60
    dig_h6 = -30
    h1 = bytes([dig_h1])
    # dig_h4 is (E4<<4)|(E5&0xF); dig_h5 is (E6<<4)|(E5>>4); both signed 12-bit.
    h4u = dig_h4 & 0xFFF
    h5u = dig_h5 & 0xFFF
    e4 = (h4u >> 4) & 0xFF
    e6 = (h5u >> 4) & 0xFF
    e5 = ((h4u & 0xF) | ((h5u & 0xF) << 4)) & 0xFF
    h2_6 = (
        dig_h2.to_bytes(2, "little", signed=True)
        + bytes([dig_h3 & 0xFF])
        + bytes([e4, e5, e6])
        + bytes([dig_h6 & 0xFF if dig_h6 >= 0 else 256 + dig_h6])
    )
    return t_p, h1, h2_6


T_P_BLOCK, H1_BYTE, H2_6_BLOCK = _calibration_bytes()


def _bus(has_humidity: bool = True, address: int = ADDRESS) -> FakeI2c:
    registers = {address: {bme280._CALIB_T_P: list(T_P_BLOCK)}}
    if has_humidity:
        registers[address][bme280._CALIB_H1] = list(H1_BYTE)
        registers[address][bme280._CALIB_H2_6] = list(H2_6_BLOCK)
    return FakeI2c(registers=registers)


class TestParseCalibration:
    def test_round_trips_signed_and_unsigned_words(self):
        cal = bme280.parse_calibration(T_P_BLOCK, H1_BYTE, H2_6_BLOCK)
        assert cal.dig_t1 == 27504
        assert cal.dig_t3 == -1000
        assert cal.dig_p2 == -10685
        assert cal.dig_p9 == 6000

    def test_h4_h5_are_unpacked_from_their_shared_byte(self):
        cal = bme280.parse_calibration(T_P_BLOCK, H1_BYTE, H2_6_BLOCK)
        assert cal.dig_h4 == -204
        assert cal.dig_h5 == 60
        assert cal.dig_h1 == 75
        assert cal.dig_h6 == -30

    def test_bmp280_has_no_humidity_words(self):
        cal = bme280.parse_calibration(T_P_BLOCK, None, None)
        assert cal.dig_h1 is None

    def test_wrong_length_blocks_are_refused(self):
        with pytest.raises(ValueError, match="24"):
            bme280.parse_calibration(T_P_BLOCK[:10], H1_BYTE, H2_6_BLOCK)
        with pytest.raises(ValueError, match="7"):
            bme280.parse_calibration(T_P_BLOCK, H1_BYTE, H2_6_BLOCK[:3])


class TestCompensate:
    def test_temperature_and_pressure_are_finite_and_in_a_sane_band(self):
        cal = bme280.parse_calibration(T_P_BLOCK, H1_BYTE, H2_6_BLOCK)
        # adc_T = 519888 is the value used in Bosch's own reference implementation notes.
        temperature, t_fine = bme280.compensate_temperature(519888, cal)
        assert temperature == pytest.approx(25.08, abs=0.1)
        pressure = bme280.compensate_pressure(415148, t_fine, cal)
        assert 30000.0 < pressure < 110000.0

    def test_humidity_is_clamped_to_0_100(self):
        cal = bme280.parse_calibration(T_P_BLOCK, H1_BYTE, H2_6_BLOCK)
        _, t_fine = bme280.compensate_temperature(519888, cal)
        assert 0.0 <= bme280.compensate_humidity(0, t_fine, cal) <= 100.0
        assert 0.0 <= bme280.compensate_humidity(65535, t_fine, cal) <= 100.0

    def test_humidity_on_a_bmp280_calibration_is_refused(self):
        cal = bme280.parse_calibration(T_P_BLOCK, None, None)
        with pytest.raises(ValueError, match="BMP280"):
            bme280.compensate_humidity(30000, 0.0, cal)

    def test_zero_dig_p1_returns_zero_rather_than_dividing_by_it(self):
        cal = bme280.parse_calibration(T_P_BLOCK, H1_BYTE, H2_6_BLOCK)._replace(dig_p1=0)
        assert bme280.compensate_pressure(400000, 0.0, cal) == 0.0


class TestCtrlWords:
    def test_ctrl_meas_packs_oversampling_and_mode(self):
        assert bme280.ctrl_meas(1, 1) == 0b001_001_01
        assert bme280.ctrl_meas(16, 16) == 0b101_101_01

    def test_max_measurement_time_grows_with_oversampling(self):
        assert bme280.max_measurement_s(0, 0, 0) == pytest.approx(0.00125)
        assert bme280.max_measurement_s(16, 16, 16) > bme280.max_measurement_s(1, 1, 1)


class TestBme280Sensor:
    def test_reads_calibration_at_construction(self):
        sensor = bme280.Bme280Sensor(_bus(), ADDRESS, sleep=False)
        assert sensor.cal.dig_h1 == 75

    def test_bmp280_mode_skips_the_humidity_block(self):
        bus = _bus(has_humidity=False)
        sensor = bme280.Bme280Sensor(bus, ADDRESS, has_humidity=False, sleep=False)
        assert sensor.cal.dig_h1 is None
        assert bme280._CALIB_H1 not in bus.registers[ADDRESS]

    def test_read_writes_ctrl_regs_then_reads_the_data_burst(self):
        bus = _bus()
        # press_msb, press_lsb, press_xlsb, temp_msb, temp_lsb, temp_xlsb, hum_msb, hum_lsb
        adc_p, adc_t, adc_h = 415148, 519888, 30000
        bus.registers[ADDRESS][bme280._DATA_BASE] = [
            (adc_p >> 12) & 0xFF,
            (adc_p >> 4) & 0xFF,
            (adc_p << 4) & 0xF0,
            (adc_t >> 12) & 0xFF,
            (adc_t >> 4) & 0xFF,
            (adc_t << 4) & 0xF0,
            (adc_h >> 8) & 0xFF,
            adc_h & 0xFF,
        ]
        sensor = bme280.Bme280Sensor(bus, ADDRESS, sleep=False)
        temperature, pressure, humidity = sensor.read()
        assert temperature == pytest.approx(25.08, abs=0.1)
        assert 30000.0 < pressure < 110000.0
        assert humidity is not None and 0.0 <= humidity <= 100.0
        assert (ADDRESS, bme280.CTRL_MEAS, [bme280.ctrl_meas(1, 1)]) in bus.written


class TestBme280Device:
    def test_bme280_has_humidity_bmp280_does_not(self):
        wet = bme280.Bme280("wet", _bus(), sleep=False)
        assert set(wet.signals) == {"conditions", "temperature", "pressure", "humidity"}
        dry = bme280.Bme280("dry", _bus(has_humidity=False), has_humidity=False, sleep=False)
        assert set(dry.signals) == {"conditions", "temperature", "pressure"}

    def test_read_yields_one_sample(self):
        bus = _bus()
        adc_p, adc_t, adc_h = 415148, 519888, 30000
        bus.registers[ADDRESS][bme280._DATA_BASE] = [
            (adc_p >> 12) & 0xFF,
            (adc_p >> 4) & 0xFF,
            (adc_p << 4) & 0xF0,
            (adc_t >> 12) & 0xFF,
            (adc_t >> 4) & 0xFF,
            (adc_t << 4) & 0xF0,
            (adc_h >> 8) & 0xFF,
            adc_h & 0xFF,
        ]
        chip = bme280.Bme280("wet", bus, sleep=False)
        (sample,) = chip.read(3)
        assert sample.node is chip.root and sample.time_ns == 3
        by_name = sample.by_name()
        assert set(by_name) == {"temperature", "pressure", "humidity"}

    def test_config_round_trips(self):
        chip = bme280.Bme280(
            "wet", _bus(), address=ADDRESS, osrs_t=2, osrs_p=4, osrs_h=8, sleep=False
        )
        cfg = chip.config
        assert cfg.address == ADDRESS
        assert (cfg.osrs_t, cfg.osrs_p, cfg.osrs_h) == (2, 4, 8)
        assert cfg.has_humidity is True

    def test_a_missing_chip_raises_at_construction(self):
        with pytest.raises(OSError):
            bme280.Bme280("wet", FakeI2c(), sleep=False)
