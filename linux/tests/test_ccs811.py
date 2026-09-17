"""CCS811: register protocol, boot sequence, and env-data encoding, against a scripted bus."""

import pytest
from flyball.core.errors import HardwareError

from flyball_linux.devices.chips import ccs811
from flyball_linux.links.i2c import FakeI2c


class BootingI2c(FakeI2c):
    """A `FakeI2c` whose STATUS register flips to app mode once `APP_START` is written.

    `FakeI2c`'s registers are a static dict, so the mandatory boot sequence
    (poll STATUS, write APP_START, poll STATUS again) needs a bus that
    reacts to the write; this is that bus, for tests only.
    """

    def write(self, address: int, data) -> None:  # noqa: ANN001
        super().write(address, data)
        if list(data) == [ccs811.APP_START]:
            self.registers.setdefault(address, {})[ccs811.STATUS] = [ccs811.STATUS_FW_MODE]


class TestProtocol:
    def test_encode_env_data_is_four_bytes(self):
        assert ccs811.encode_env_data(50.0, 25.0) == [100, 0, 100, 0]

    def test_encode_env_data_carries_sub_percent_resolution(self):
        hi, lo, *_ = ccs811.encode_env_data(50.6, 25.0)
        assert (hi << 8 | lo) == round(50.6 * 512.0)

    def test_decode_alg_result_splits_co2_and_tvoc(self):
        assert ccs811.decode_alg_result(bytes([0x01, 0x90, 0x00, 0x32])) == (0x0190, 0x0032)

    def test_decode_alg_result_rejects_a_short_reply(self):
        with pytest.raises(HardwareError, match="4"):
            ccs811.decode_alg_result(bytes([0x01, 0x90]))


class TestCcs811Sensor:
    def _booted_bus(self) -> BootingI2c:
        return BootingI2c(registers={ccs811.CCS811_ADDRESS: {ccs811.STATUS: [0x00]}})

    def test_boot_writes_app_start_then_meas_mode(self):
        bus = self._booted_bus()
        sensor = ccs811.Ccs811Sensor(bus, sleep=False)
        sensor.boot()
        assert bus.written[0] == (ccs811.CCS811_ADDRESS, None, [ccs811.APP_START])
        assert bus.written[1] == (ccs811.CCS811_ADDRESS, ccs811.MEAS_MODE, [ccs811.DRIVE_MODE_1S])

    def test_boot_is_a_no_op_if_already_in_app_mode(self):
        bus = FakeI2c(registers={ccs811.CCS811_ADDRESS: {ccs811.STATUS: [ccs811.STATUS_FW_MODE]}})
        sensor = ccs811.Ccs811Sensor(bus, sleep=False)
        sensor.boot()
        assert bus.written == [(ccs811.CCS811_ADDRESS, ccs811.MEAS_MODE, [ccs811.DRIVE_MODE_1S])]

    def test_boot_raises_if_the_chip_never_reports_app_mode(self):
        bus = FakeI2c(registers={ccs811.CCS811_ADDRESS: {ccs811.STATUS: [0x00]}})
        sensor = ccs811.Ccs811Sensor(bus, sleep=False)
        with pytest.raises(HardwareError, match="boot mode"):
            sensor.boot()

    def test_measure_before_boot_is_a_hardware_error(self):
        bus = FakeI2c(registers={ccs811.CCS811_ADDRESS: {ccs811.STATUS: [0x00]}})
        sensor = ccs811.Ccs811Sensor(bus, sleep=False)
        with pytest.raises(HardwareError, match="APP_START"):
            sensor.measure()

    def test_measure_reads_alg_result_data_once_booted(self):
        bus = BootingI2c(
            registers={
                ccs811.CCS811_ADDRESS: {
                    ccs811.STATUS: [ccs811.STATUS_FW_MODE],
                    ccs811.ALG_RESULT_DATA: [0x01, 0x90, 0x00, 0x32],
                }
            }
        )
        sensor = ccs811.Ccs811Sensor(bus, sleep=False)
        sensor.boot()
        assert sensor.measure() == (0x0190, 0x0032)

    def test_a_reported_error_is_a_hardware_error(self):
        bus = FakeI2c(
            registers={
                ccs811.CCS811_ADDRESS: {
                    ccs811.STATUS: [ccs811.STATUS_FW_MODE | ccs811.STATUS_ERROR],
                    ccs811.ERROR_ID: [0x04],
                }
            }
        )
        sensor = ccs811.Ccs811Sensor(bus, sleep=False)
        with pytest.raises(HardwareError, match="ERROR_ID"):
            sensor.measure()

    def test_set_environment_writes_env_data(self):
        bus = FakeI2c(registers={ccs811.CCS811_ADDRESS: {ccs811.STATUS: [ccs811.STATUS_FW_MODE]}})
        sensor = ccs811.Ccs811Sensor(bus, sleep=False)
        sensor.set_environment(50.0, 25.0)
        assert bus.written == [(ccs811.CCS811_ADDRESS, ccs811.ENV_DATA, [100, 0, 100, 0])]

    def test_a_missing_chip_raises_so_the_device_goes_offline(self):
        sensor = ccs811.Ccs811Sensor(FakeI2c(), sleep=False)
        with pytest.raises(OSError):
            sensor.boot()


class TestCcs811Device:
    def test_construction_runs_the_boot_sequence(self):
        bus = BootingI2c(registers={ccs811.CCS811_ADDRESS: {ccs811.STATUS: [0x00]}})
        gas = ccs811.Ccs811("gas", bus, sleep=False)
        assert (ccs811.CCS811_ADDRESS, None, [ccs811.APP_START]) in bus.written
        assert {p: str(s.access) for p, s in gas.signals.items()} == {
            "conditions": "rp",
            "co2eq": "rp",
            "tvoc": "rp",
        }

    def test_construction_raises_if_boot_never_completes(self):
        bus = FakeI2c(registers={ccs811.CCS811_ADDRESS: {ccs811.STATUS: [0x00]}})
        with pytest.raises(HardwareError, match="boot mode"):
            ccs811.Ccs811("gas", bus, sleep=False)

    def test_read_collects_one_sample(self):
        bus = BootingI2c(
            registers={
                ccs811.CCS811_ADDRESS: {
                    ccs811.STATUS: [0x00],
                    ccs811.ALG_RESULT_DATA: [0x01, 0x90, 0x00, 0x32],
                }
            }
        )
        gas = ccs811.Ccs811("gas", bus, sleep=False)
        (sample,) = gas.read(9)
        assert sample.node is gas.root and sample.time_ns == 9
        assert sample.by_name() == {"co2eq": 0x0190, "tvoc": 0x0032}
        assert gas.config.address == ccs811.CCS811_ADDRESS
