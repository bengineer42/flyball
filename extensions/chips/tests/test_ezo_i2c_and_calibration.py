"""EZO I2C transport, calibration commands and temperature/S/P compensation.

`test_ezo_ph.py` and `test_ezo_family.py` cover the original UART-only `R` exchange;
this file covers what E4 (`sensor-generalisation.md`) added: the I2C transport (status
codes, the processing wait), calibration command strings over both transports, and a
compensated read with a bound `temperature` input, with and without a value on it.
"""

from __future__ import annotations

import pytest
from flyball.foundation.errors import HardwareError
from flyball_sim.links import FakeI2c, FakeUart

from flyball_chips import _ezo, ezo_do, ezo_ec, ezo_orp, ezo_ph


def _i2c_reply(status: int, text: str = "") -> list[int]:
    """A scripted I2C reply: the status byte, the text, a trailing NULL."""
    return [status, *text.encode("ascii"), 0]


class TestI2cStatusCodes:
    """`_ezo.i2c_exchange`: the status byte (pH datasheet p.30)."""

    def test_success_returns_a_cr_terminated_frame_like_uart(self):
        i2c = FakeI2c(replies={0x63: [_i2c_reply(1, "7.002")]}, short_reads=True)
        frame = _ezo.i2c_exchange(i2c, 0x63, b"R", sleep=False, delay_s=0.0, chip="EZO-pH")
        assert frame == b"7.002\r"

    def test_syntax_error_raises(self):
        i2c = FakeI2c(replies={0x63: [_i2c_reply(2)]}, short_reads=True)
        with pytest.raises(HardwareError, match="syntax error"):
            _ezo.i2c_exchange(i2c, 0x63, b"cal,mid,7.00", sleep=False, delay_s=0.0, chip="EZO-pH")

    def test_still_processing_raises(self):
        i2c = FakeI2c(replies={0x63: [_i2c_reply(254)]}, short_reads=True)
        with pytest.raises(HardwareError, match="still processing"):
            _ezo.i2c_exchange(i2c, 0x63, b"R", sleep=False, delay_s=0.0, chip="EZO-pH")

    def test_no_data_raises(self):
        i2c = FakeI2c(replies={0x63: [_i2c_reply(255)]}, short_reads=True)
        with pytest.raises(HardwareError, match="no data"):
            _ezo.i2c_exchange(i2c, 0x63, b"R", sleep=False, delay_s=0.0, chip="EZO-pH")

    def test_an_empty_reply_raises(self):
        i2c = FakeI2c(replies={0x63: [[]]}, short_reads=True)
        with pytest.raises(HardwareError, match="empty"):
            _ezo.i2c_exchange(i2c, 0x63, b"R", sleep=False, delay_s=0.0, chip="EZO-pH")


class TestI2cProcessingWait:
    """`i2c_exchange` waits `delay_s` before reading back, unless `sleep=False`."""

    def test_waits_delay_s_when_sleep_is_true(self, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(_ezo.time, "sleep", lambda s: slept.append(s))
        i2c = FakeI2c(replies={0x63: [_i2c_reply(1, "7.002")]}, short_reads=True)
        _ezo.i2c_exchange(i2c, 0x63, b"R", sleep=True, delay_s=0.9, chip="EZO-pH")
        assert slept == [0.9]

    def test_does_not_wait_when_sleep_is_false(self, monkeypatch):
        monkeypatch.setattr(
            _ezo.time, "sleep", lambda s: pytest.fail("should not sleep with sleep=False")
        )
        i2c = FakeI2c(replies={0x63: [_i2c_reply(1, "7.002")]}, short_reads=True)
        _ezo.i2c_exchange(i2c, 0x63, b"R", sleep=False, delay_s=0.9, chip="EZO-pH")


class TestEzoTransport:
    """`EzoTransport` picks its wire protocol from what `link` is."""

    def test_picks_i2c_from_the_link_type(self):
        i2c = FakeI2c(replies={0x63: [_i2c_reply(1, "7.002")]}, short_reads=True)
        transport = _ezo.EzoTransport(i2c, "EZO-pH", address=0x63, sleep=False)
        assert transport.is_i2c
        assert transport.read(b"R", 0.0) == b"7.002\r"
        assert i2c.written == [(0x63, None, list(b"R"))]

    def test_picks_uart_from_the_link_type(self):
        uart = FakeUart([b"7.002\r"])
        transport = _ezo.EzoTransport(uart, "EZO-pH", sleep=False)
        assert not transport.is_i2c
        assert transport.read(b"R", 0.0) == b"7.002\r"
        assert uart.written == [b"R\r"]

    def test_write_only_requires_the_i2c_status_byte(self):
        i2c = FakeI2c(replies={0x63: [_i2c_reply(1)]}, short_reads=True)
        transport = _ezo.EzoTransport(i2c, "EZO-pH", address=0x63, sleep=False)
        transport.write(b"cal,clear", 0.0)  # raises if it misreads the (absent) text

    def test_write_raises_on_an_unacknowledged_uart_reply(self):
        uart = FakeUart([b"*ER\r"])
        transport = _ezo.EzoTransport(uart, "EZO-pH", sleep=False)
        with pytest.raises(HardwareError, match="not acknowledged"):
            transport.write(b"cal,clear", 0.0)


class TestEzoPhCalibration:
    def test_calibrate_mid_over_uart(self):
        uart = FakeUart([b"*OK\r"])
        probe = ezo_ph.EzoPhProbe(uart, sleep=False)
        probe.calibrate_mid(7.0)
        assert uart.written == [b"cal,mid,7.0\r"]

    def test_calibrate_low_and_high_over_i2c(self):
        i2c = FakeI2c(replies={0x63: [_i2c_reply(1), _i2c_reply(1)]}, short_reads=True)
        probe = ezo_ph.EzoPhProbe(i2c, sleep=False)
        probe.calibrate_low(4.0)
        probe.calibrate_high(10.0)
        assert i2c.written == [
            (0x63, None, list(b"cal,low,4.0")),
            (0x63, None, list(b"cal,high,10.0")),
        ]

    def test_calibrate_clear_over_uart(self):
        uart = FakeUart([b"*OK\r"])
        probe = ezo_ph.EzoPhProbe(uart, sleep=False)
        probe.calibrate_clear()
        assert uart.written == [b"cal,clear\r"]

    def test_calibration_status_parses_the_reply(self):
        i2c = FakeI2c(replies={0x63: [_i2c_reply(1, "?CAL,2")]}, short_reads=True)
        probe = ezo_ph.EzoPhProbe(i2c, sleep=False)
        assert probe.calibration_status() == 2

    def test_calibration_status_rejects_a_garbled_reply(self):
        uart = FakeUart([b"garbage\r"])
        probe = ezo_ph.EzoPhProbe(uart, sleep=False)
        with pytest.raises(HardwareError, match="CAL"):
            probe.calibration_status()

    def test_commands_are_registered_long_and_without_writes(self):
        for name in ("calibrate_mid", "calibrate_low", "calibrate_high", "calibrate_clear"):
            spec = ezo_ph.EzoPh.commands[name]
            assert spec.long is True
            assert spec.writes == ()


class TestEzoEcCalibration:
    def test_calibrate_dry_over_uart(self):
        uart = FakeUart([b"*OK\r"])
        probe = ezo_ec.EzoEcProbe(uart, sleep=False)
        probe.calibrate_dry()
        assert uart.written == [b"cal,dry\r"]

    def test_calibrate_low_and_high_over_i2c(self):
        i2c = FakeI2c(replies={0x64: [_i2c_reply(1), _i2c_reply(1)]}, short_reads=True)
        probe = ezo_ec.EzoEcProbe(i2c, sleep=False)
        probe.calibrate_low(12880.0)
        probe.calibrate_high(80000.0)
        assert i2c.written == [
            (0x64, None, list(b"cal,low,12880.0")),
            (0x64, None, list(b"cal,high,80000.0")),
        ]

    def test_calibrate_clear_over_uart(self):
        uart = FakeUart([b"*OK\r"])
        probe = ezo_ec.EzoEcProbe(uart, sleep=False)
        probe.calibrate_clear()
        assert uart.written == [b"cal,clear\r"]

    def test_set_cell_constant_over_i2c(self):
        i2c = FakeI2c(replies={0x64: [_i2c_reply(1)]}, short_reads=True)
        probe = ezo_ec.EzoEcProbe(i2c, sleep=False)
        probe.set_cell_constant(1.0)
        assert i2c.written == [(0x64, None, list(b"K,1.0"))]


class TestEzoDoCalibration:
    def test_calibrate_over_uart(self):
        uart = FakeUart([b"*OK\r"])
        probe = ezo_do.EzoDoProbe(uart, sleep=False)
        probe.calibrate()
        assert uart.written == [b"cal\r"]

    def test_calibrate_zero_over_i2c(self):
        i2c = FakeI2c(replies={0x61: [_i2c_reply(1)]}, short_reads=True)
        probe = ezo_do.EzoDoProbe(i2c, sleep=False)
        probe.calibrate_zero()
        assert i2c.written == [(0x61, None, list(b"cal,0"))]

    def test_calibrate_clear_over_uart(self):
        uart = FakeUart([b"*OK\r"])
        probe = ezo_do.EzoDoProbe(uart, sleep=False)
        probe.calibrate_clear()
        assert uart.written == [b"cal,clear\r"]


class TestEzoOrpCalibration:
    def test_calibrate_over_uart(self):
        uart = FakeUart([b"*OK\r"])
        probe = ezo_orp.EzoOrpProbe(uart, sleep=False)
        probe.calibrate(225.0)
        assert uart.written == [b"cal,225.0\r"]

    def test_calibrate_clear_over_i2c(self):
        i2c = FakeI2c(replies={0x62: [_i2c_reply(1)]}, short_reads=True)
        probe = ezo_orp.EzoOrpProbe(i2c, sleep=False)
        probe.calibrate_clear()
        assert i2c.written == [(0x62, None, list(b"cal,clear"))]

    def test_has_no_temperature_input(self):
        assert "temperature" not in ezo_orp.EzoOrp.INPUTS


class TestTemperatureCompensation:
    def test_ph_sends_t_before_r_when_bound_and_valued(self):
        uart = FakeUart([b"*OK\r", b"6.850\r"])
        device = ezo_ph.EzoPh("water", uart, sleep=False)
        device.temperature.attach(24.5)
        (sample,) = device.read(9)
        assert uart.written == [b"T,24.5\r", b"R\r"]
        assert sample.by_name()["ph"] == pytest.approx(6.850)
        assert device.held_conditions() == []

    def test_ph_reads_uncompensated_with_no_temperature_value(self):
        uart = FakeUart([b"6.850\r"])
        device = ezo_ph.EzoPh("water", uart, sleep=False)
        (sample,) = device.read(9)
        assert uart.written == [b"R\r"]
        assert sample.by_name()["ph"] == pytest.approx(6.850)
        codes = {c.code for c in device.held_conditions()}
        assert "uncompensated" in codes

    def test_ph_clears_uncompensated_once_a_value_arrives(self):
        uart = FakeUart([b"6.850\r", b"*OK\r", b"6.850\r"])
        device = ezo_ph.EzoPh("water", uart, sleep=False)
        list(device.read(9))
        assert "uncompensated" in {c.code for c in device.held_conditions()}
        device.temperature.attach(20.0)
        list(device.read(10))
        assert device.held_conditions() == []

    def test_do_uses_the_combined_rt_command_when_bound(self):
        uart = FakeUart([b"8.91\r"])
        device = ezo_do.EzoDo("tank", uart, sleep=False)
        device.temperature.attach(19.5)
        (sample,) = device.read(9)
        assert uart.written == [b"RT,19.5\r"]
        assert sample.by_name()["dissolved_oxygen"] == pytest.approx(8.91)

    def test_do_reads_uncompensated_with_no_temperature_value(self):
        uart = FakeUart([b"7.82\r"])
        device = ezo_do.EzoDo("tank", uart, sleep=False)
        (sample,) = device.read(9)
        assert uart.written == [b"R\r"]
        assert "uncompensated" in {c.code for c in device.held_conditions()}

    def test_do_salinity_and_pressure_inputs_are_sent_before_the_read(self):
        uart = FakeUart([b"*OK\r", b"*OK\r", b"7.82\r"])
        device = ezo_do.EzoDo("tank", uart, sleep=False)
        device.salinity.attach(50000.0)
        device.pressure.attach(101.3)
        list(device.read(9))
        assert uart.written == [b"S,50000.0\r", b"P,101.3\r", b"R\r"]

    def test_ec_uses_the_combined_rt_command_when_bound(self):
        uart = FakeUart([b"1413.000,706.500,0.700,1.000\r"])
        device = ezo_ec.EzoEc("water", uart, sleep=False)
        device.temperature.attach(25.0)
        (sample,) = device.read(9)
        assert uart.written == [b"RT,25.0\r"]
        assert sample.by_name()["conductivity"] == pytest.approx(1413.000)

    def test_ec_reads_uncompensated_with_no_temperature_value(self):
        uart = FakeUart([b"1413.000,706.500,0.700,1.000\r"])
        device = ezo_ec.EzoEc("water", uart, sleep=False)
        list(device.read(9))
        assert uart.written == [b"R\r"]
        assert "uncompensated" in {c.code for c in device.held_conditions()}
