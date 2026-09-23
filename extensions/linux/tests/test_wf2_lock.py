"""The rig-lock rule for the long commands here: `dispense` and `move` wait off the rig lock.

A `stop` issued while one runs takes effect at once, rather than queueing behind it.
"""

from __future__ import annotations

import threading
import time

from flyball.rig import Rig
from flyball_sim import SteppedClock

from flyball_linux.devices.dosing_pump import DosingPump
from flyball_linux.devices.pwm import PwmChannel
from flyball_linux.devices.stepper import Stepper
from flyball_linux.links.gpio import FakeGpio
from flyball_linux.links.pwm import FakePwm


def _pump(rig: Rig) -> tuple[DosingPump, FakePwm]:
    pwm = FakePwm()
    pump = DosingPump("dosing", PwmChannel("motor", pwm, 0, frequency_hz=1000), ml_per_s=1.0)
    rig.add_device(pump)
    return pump, pwm


def _running(rig: Rig, device: object, command: str, args: dict) -> threading.Thread:
    thread = threading.Thread(target=rig.run_command, args=(device, command, args), daemon=True)
    thread.start()
    return thread


def test_a_stop_during_a_dispense_cuts_the_pump_at_once() -> None:
    rig = Rig()  # the wall clock: a 30 ml dose at 1 ml/s would really take 30 s
    pump, pwm = _pump(rig)
    thread = _running(rig, pump, "dispense", {"volume_ml": 30.0})
    deadline = time.monotonic() + 2.0
    while pwm.channels[0][1] == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert pwm.channels[0][1] > 0, "the pump is running"
    time.sleep(0.1)
    began = time.monotonic()
    rig.run_command(pump, "stop")
    assert pwm.channels[0][1] == 0, "stop cut the pump"
    thread.join(2.0)
    assert not thread.is_alive(), "the dispense ended, not after 30 s"
    assert time.monotonic() - began < 1.0
    dosed = rig.router.value(pump.signals["dispensed_ml"])
    assert 0.0 < dosed < 5.0, "credited with what ran, not the 30 ml asked for"
    rig.close()


def test_a_dispense_on_a_stepped_clock_steps_its_dose() -> None:
    rig = Rig()
    rig.clock = SteppedClock(0)
    pump, pwm = _pump(rig)
    rig.run_command(pump, "dispense", {"volume_ml": 120.0})
    assert rig.clock.now_ns() == 120 * 10**9, "two minutes of the rig's time, stepped at once"
    assert pwm.channels[0][1] == 0
    assert rig.router.value(pump.signals["dispensed_ml"]) == 120.0


def test_a_stop_during_a_move_ends_it_after_the_pulse_in_progress() -> None:
    rig = Rig()
    chip = FakeGpio()
    motor = Stepper("motor", chip, step_line=0, direction_line=1, steps_per_s=100, enable_line=2)
    rig.add_device(motor)
    thread = _running(rig, motor, "move", {"steps": 100_000})  # 1000 s at 100 steps/s
    deadline = time.monotonic() + 2.0
    while motor._position < 3 and time.monotonic() < deadline:
        time.sleep(0.005)
    began = time.monotonic()
    rig.run_command(motor, "stop")
    thread.join(2.0)
    assert not thread.is_alive() and time.monotonic() - began < 1.0
    assert 3 <= motor._position < 1000
    assert chip.get(2) is True, "enable released"
    rig.close()
