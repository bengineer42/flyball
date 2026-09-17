"""DosingPump: calibration math, and that dispense always stops the pump."""

from __future__ import annotations

import pytest

from flyball_linux.devices.dosing_pump import DosingPump
from flyball_linux.devices.gpio import GpioLine
from flyball_linux.devices.pwm import PwmChannel
from flyball_linux.links.gpio import FakeGpio
from flyball_linux.links.pwm import FakePwm


def make_pwm_pump(monkeypatch, ml_per_s=2.0, drive_fraction=1.0, max_dispense_ml=None):
    pwm = FakePwm()
    channel = PwmChannel("motor", pwm, 0, frequency_hz=1000)
    slept: list[float] = []
    monkeypatch.setattr(
        "flyball_linux.devices.dosing_pump.time.sleep", lambda s: slept.append(s)
    )
    pump = DosingPump(
        "dosing", channel, ml_per_s, drive_fraction, max_dispense_ml, label=None
    )
    return pump, pwm, slept


def make_gpio_pump(monkeypatch, ml_per_s=2.0, max_dispense_ml=None):
    chip = FakeGpio()
    line = GpioLine("motor", chip, 3)
    slept: list[float] = []
    monkeypatch.setattr(
        "flyball_linux.devices.dosing_pump.time.sleep", lambda s: slept.append(s)
    )
    pump = DosingPump("dosing", line, ml_per_s, max_dispense_ml=max_dispense_ml)
    return pump, chip, slept


class TestCalibration:
    def test_duration_is_volume_over_rate(self, monkeypatch):
        pump, _, _ = make_pwm_pump(monkeypatch, ml_per_s=2.0)
        assert pump.duration_s(5.0) == pytest.approx(2.5)

    def test_dispense_sleeps_for_the_calculated_duration(self, monkeypatch):
        pump, pwm, slept = make_pwm_pump(monkeypatch, ml_per_s=4.0)
        pump.dispense(10.0)
        assert slept == [pytest.approx(2.5)]

    def test_dispense_tracks_the_running_total(self, monkeypatch):
        pump, _, _ = make_pwm_pump(monkeypatch, ml_per_s=1.0)
        pump.dispense(3.0)
        pump.dispense(2.0)
        assert pump.signals["dispensed_ml"].value == pytest.approx(5.0)

    def test_max_dispense_ml_is_enforced(self, monkeypatch):
        pump, _, _ = make_pwm_pump(monkeypatch, ml_per_s=1.0, max_dispense_ml=5.0)
        with pytest.raises(ValueError, match="exceeds max_dispense_ml"):
            pump.dispense(6.0)

    def test_non_positive_volume_is_refused(self, monkeypatch):
        pump, _, _ = make_pwm_pump(monkeypatch)
        with pytest.raises(ValueError):
            pump.dispense(0.0)
        with pytest.raises(ValueError):
            pump.dispense(-1.0)

    def test_bad_calibration_is_refused_at_construction(self, monkeypatch):
        pwm = FakePwm()
        channel = PwmChannel("motor", pwm, 0)
        with pytest.raises(ValueError, match="ml_per_s"):
            DosingPump("dosing", channel, 0.0)
        with pytest.raises(ValueError, match="drive_fraction"):
            DosingPump("dosing", channel, 1.0, drive_fraction=0.0)


class TestPwmDrivenPump:
    def test_dispense_drives_then_stops(self, monkeypatch):
        pump, pwm, _ = make_pwm_pump(monkeypatch, ml_per_s=1.0, drive_fraction=0.5)
        seen_mid_sleep = {}

        def sleeping(s):
            # capture the duty mid-run, before dispense's finally turns it off
            seen_mid_sleep["duty"] = pwm.channels[0][1] / pwm.channels[0][0]

        monkeypatch.setattr("flyball_linux.devices.dosing_pump.time.sleep", sleeping)
        pump.dispense(2.0)
        assert seen_mid_sleep["duty"] == pytest.approx(0.5), "ran at drive_fraction while dosing"
        assert pwm.channels[0][1] == 0, "stopped after the run"

    def test_dispense_stops_the_pump_even_when_sleep_raises(self, monkeypatch):
        pump, pwm, _ = make_pwm_pump(monkeypatch, ml_per_s=1.0)

        def boom(s):
            raise RuntimeError("simulated interrupt mid-dispense")

        monkeypatch.setattr("flyball_linux.devices.dosing_pump.time.sleep", boom)
        with pytest.raises(RuntimeError, match="simulated interrupt"):
            pump.dispense(2.0)
        assert pwm.channels[0][1] == 0, "the pump was switched off despite the failure"
        assert pump.signals["dispensed_ml"].value == 0.0, "no volume credited on a failed run"

    def test_stop_switches_the_pump_off(self, monkeypatch):
        pump, pwm, _ = make_pwm_pump(monkeypatch, ml_per_s=1.0)
        pump._run(True)
        assert pwm.channels[0][1] > 0
        pump.stop()
        assert pwm.channels[0][1] == 0


class TestGpioDrivenPump:
    def test_dispense_switches_on_then_off(self, monkeypatch):
        pump, chip, _ = make_gpio_pump(monkeypatch, ml_per_s=1.0)
        seen_mid_sleep = {}

        def sleeping(s):
            seen_mid_sleep["on"] = chip.get(3)

        monkeypatch.setattr("flyball_linux.devices.dosing_pump.time.sleep", sleeping)
        pump.dispense(2.0)
        assert seen_mid_sleep["on"] is True
        assert chip.get(3) is False, "off after the run"

    def test_dispense_stops_the_pump_even_when_sleep_raises(self, monkeypatch):
        pump, chip, _ = make_gpio_pump(monkeypatch, ml_per_s=1.0)

        def boom(s):
            raise RuntimeError("simulated interrupt")

        monkeypatch.setattr("flyball_linux.devices.dosing_pump.time.sleep", boom)
        with pytest.raises(RuntimeError):
            pump.dispense(2.0)
        assert chip.get(3) is False, "the relay was switched off despite the failure"
