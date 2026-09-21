"""Stepper: step-pulse count/timing math, direction, the safety-on-error guarantee, and access."""

from __future__ import annotations

import pytest
from flyball.foundation.device import Access

from flyball_linux.devices.stepper import Stepper
from flyball_linux.links.gpio import FakeGpio


def make_stepper(
    monkeypatch,
    steps_per_s=100.0,
    enable_line=None,
    steps_per_unit=None,
    pulse_width_s=0.0005,
):
    chip = FakeGpio()
    slept: list[float] = []
    monkeypatch.setattr("flyball_linux.devices.stepper.time.sleep", lambda s: slept.append(s))
    motor = Stepper(
        "motor",
        chip,
        step_line=0,
        direction_line=1,
        steps_per_s=steps_per_s,
        enable_line=enable_line,
        steps_per_unit=steps_per_unit,
        pulse_width_s=pulse_width_s,
    )
    return motor, chip, slept


class TestPulseCountAndTiming:
    def test_move_pulses_the_step_line_once_per_step(self, monkeypatch):
        motor, chip, _ = make_stepper(monkeypatch)
        motor.move(5)
        # each pulse is a True then a False set on the step line
        step_sets = [v for line, v in chip.sets if line == 0]
        assert step_sets.count(True) == 5
        assert step_sets.count(False) == 5

    def test_move_sleeps_between_pulses_at_steps_per_s(self, monkeypatch):
        motor, _, slept = make_stepper(monkeypatch, steps_per_s=100.0, pulse_width_s=0.0)
        motor.move(4)
        # 4 steps -> 3 inter-pulse sleeps of 1/100s (no sleep after the last pulse)
        assert slept == [pytest.approx(0.01)] * 3

    def test_move_tracks_position(self, monkeypatch):
        motor, _, _ = make_stepper(monkeypatch)
        motor.move(7)
        assert motor._position == 7
        motor.move(-3)
        assert motor._position == 4

    def test_steps_per_unit_converts_engineering_units(self, monkeypatch):
        motor, _, _ = make_stepper(monkeypatch, steps_per_unit=20.0)
        motor.move(2.0)  # 2 units * 20 steps/unit = 40 steps
        assert motor._position == 40

    def test_zero_steps_is_a_no_op(self, monkeypatch):
        motor, chip, slept = make_stepper(monkeypatch)
        motor.move(0)
        assert chip.sets == []
        assert slept == []


class TestDirection:
    def test_forward_move_sets_direction_high(self, monkeypatch):
        motor, chip, _ = make_stepper(monkeypatch)
        motor.move(3)
        assert chip.get(1) is True

    def test_reverse_move_sets_direction_low(self, monkeypatch):
        motor, chip, _ = make_stepper(monkeypatch)
        motor.move(-3)
        assert chip.get(1) is False

    def test_direction_is_set_once_before_pulsing(self, monkeypatch):
        motor, chip, _ = make_stepper(monkeypatch)
        motor.move(3)
        direction_sets = [v for line, v in chip.sets if line == 1]
        assert direction_sets == [True]


class TestSafetyOnError:
    def test_enable_is_released_when_a_pulse_mid_move_fails(self, monkeypatch):
        motor, chip, _ = make_stepper(monkeypatch, enable_line=2)

        calls = {"n": 0}
        real_set = chip.set

        def failing_set(line, value):
            if line == 0 and value is True:
                calls["n"] += 1
                if calls["n"] == 3:
                    raise RuntimeError("simulated driver fault mid-move")
            real_set(line, value)

        monkeypatch.setattr(chip, "set", failing_set)
        with pytest.raises(RuntimeError, match="simulated driver fault"):
            motor.move(10)
        assert chip.get(2) is True, "enable was released (electrical high) despite the failure"

    def test_stop_releases_enable_immediately(self, monkeypatch):
        motor, chip, _ = make_stepper(monkeypatch, enable_line=2)
        motor._enable(True)
        assert chip.get(2) is False, "active-low enable: engaged is electrical low"
        motor.stop()
        assert chip.get(2) is True, "released is electrical high"

    def test_no_enable_line_is_a_clean_no_op(self, monkeypatch):
        motor, chip, _ = make_stepper(monkeypatch, enable_line=None)
        motor.move(2)  # should not raise, no enable line claimed or set
        assert 2 not in chip.claimed


class TestEnableLine:
    def test_enable_is_active_low_by_default_during_move(self, monkeypatch):
        motor, chip, _ = make_stepper(monkeypatch, enable_line=2)
        seen = {}
        real_pulse = motor._pulse

        def spying_pulse():
            seen.setdefault("enabled_during_move", chip.get(2))
            real_pulse()

        monkeypatch.setattr(motor, "_pulse", spying_pulse)
        motor.move(3)
        assert seen["enabled_during_move"] is False, "active-low enable is driven low during a move"
        assert chip.get(2) is True, "released (electrical high) after the move"


class TestPositionAccess:
    def test_position_is_readable_but_not_published(self, monkeypatch):
        motor, _, _ = make_stepper(monkeypatch)
        access = motor.signals["position"].access
        assert Access.R in access
        assert Access.P not in access
        assert access == Access.R
