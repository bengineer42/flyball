"""MS5611: PROM calibration, D1/D2 conversion and compensation, against a scripted bus."""

import pytest
from flyball.foundation.errors import HardwareError
from flyball_sim.links import FakeI2c

from flyball_chips import ms5611

# The MS5611-01BA03 datasheet's own worked example.
COEFFICIENTS = (40127, 36924, 23317, 23282, 33464, 28312)
D1 = 9085466
D2 = 8569150
EXPECTED_PRESSURE_PA = 100009.0
EXPECTED_TEMPERATURE_C = 20.07


def _prom_bus(address: int = ms5611.MS5611_ADDRESS) -> FakeI2c:
    """A bus that answers each PROM word command with its coefficient, in order."""
    return FakeI2c(
        replies={address: [list(c.to_bytes(2, "big")) for c in COEFFICIENTS]},
    )


def _d1d2_bytes() -> tuple[list[int], list[int]]:
    return list(D1.to_bytes(3, "big")), list(D2.to_bytes(3, "big"))


class TestCompensate:
    def test_matches_the_datasheet_worked_example(self):
        pressure, temperature = ms5611.compensate(D1, D2, COEFFICIENTS)
        assert pressure == pytest.approx(EXPECTED_PRESSURE_PA, abs=0.01)
        assert temperature == pytest.approx(EXPECTED_TEMPERATURE_C, abs=0.01)


class TestPromCommand:
    def test_words_0_to_7(self):
        assert ms5611.prom_command(0) == 0xA0
        assert ms5611.prom_command(7) == 0xAE

    def test_out_of_range_is_refused(self):
        with pytest.raises(ValueError, match="0..7"):
            ms5611.prom_command(8)


class TestMs5611Sensor:
    def test_reads_prom_at_construction(self):
        bus = _prom_bus()
        sensor = ms5611.Ms5611Sensor(bus, ms5611.MS5611_ADDRESS, sleep=False)
        assert sensor.coefficients == COEFFICIENTS
        assert (ms5611.MS5611_ADDRESS, None, [ms5611.RESET]) in bus.written

    def test_read_converts_d1_then_d2_and_compensates(self):
        bus = _prom_bus()
        d1_bytes, d2_bytes = _d1d2_bytes()
        # PROM replies are consumed first (6 words), then the D1 ADC read, then D2.
        bus.replies[ms5611.MS5611_ADDRESS] += [d1_bytes, d2_bytes]
        sensor = ms5611.Ms5611Sensor(bus, ms5611.MS5611_ADDRESS, sleep=False)
        pressure, temperature = sensor.read()
        assert pressure == pytest.approx(EXPECTED_PRESSURE_PA, abs=0.01)
        assert temperature == pytest.approx(EXPECTED_TEMPERATURE_C, abs=0.01)

    def test_a_short_adc_reply_is_a_hardware_error(self):
        bus = _prom_bus()
        bus.replies[ms5611.MS5611_ADDRESS] += [[0x00, 0x01]]  # only 2 bytes, not 3
        sensor = ms5611.Ms5611Sensor(bus, ms5611.MS5611_ADDRESS, sleep=False)
        with pytest.raises(HardwareError, match="not 3"):
            sensor.read()


class TestMs5611Device:
    def test_declares_pressure_and_temperature_on_the_root(self):
        bus = _prom_bus()
        baro = ms5611.Ms5611("baro", bus, sleep=False)
        assert {p: str(s.access) for p, s in baro.signals.items()} == {
            "conditions": "rp",
            "pressure": "rp",
            "temperature": "rp",
        }
        assert baro.signals["pressure"].unit.symbol == "Pa"
        assert baro.signals["temperature"].unit.symbol == "°C"

    def test_read_yields_one_sample(self):
        bus = _prom_bus()
        d1_bytes, d2_bytes = _d1d2_bytes()
        bus.replies[ms5611.MS5611_ADDRESS] += [d1_bytes, d2_bytes]
        baro = ms5611.Ms5611("baro", bus, sleep=False)
        (sample,) = baro.read(7)
        assert sample.node is baro.root and sample.time_ns == 7
        assert sample.by_name() == {
            "pressure": pytest.approx(EXPECTED_PRESSURE_PA, abs=0.01),
            "temperature": pytest.approx(EXPECTED_TEMPERATURE_C, abs=0.01),
        }

    def test_config_round_trips_address_and_osr(self):
        bus = _prom_bus(0x76)
        baro = ms5611.Ms5611("baro", bus, address=0x76, osr=1024, sleep=False)
        assert baro.config.address == 0x76
        assert baro.config.osr == 1024

    def test_a_missing_chip_raises_at_construction(self):
        with pytest.raises(OSError):
            ms5611.Ms5611("baro", FakeI2c(), sleep=False)
