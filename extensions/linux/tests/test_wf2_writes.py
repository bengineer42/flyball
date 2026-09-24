"""`writes=`: gpio `on`/`off` and pwm `off` are refused while a controller drives the line."""

from __future__ import annotations

import pytest
from flyball.control.laws import P
from flyball.foundation.device import Readable, Readout
from flyball.foundation.errors import ConflictError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius
from flyball.rig import Rig
from flyball_sim import SteppedClock

from flyball_linux.devices.dosing_pump import DosingPump
from flyball_linux.devices.gpio import GpioLine
from flyball_linux.devices.pwm import PwmChannel
from flyball_linux.links.gpio import FakeGpio
from flyball_linux.links.pwm import FakePwm

TEMP = Quantity("temperature", Celsius)


class Probe(Readable):
    temperature = Readout("temperature", "Temperature", TEMP)

    def read(self, time_ns, node=None):
        yield self.sample(time_ns, temperature=20.0)


@pytest.fixture
def rig() -> Rig:
    rig = Rig()
    rig.clock = SteppedClock(0)
    rig.add_device(Probe("probe"))
    return rig


def _regulate(rig: Rig, output) -> object:
    controller = rig.attach_controller(
        output, rig.devices["probe"].signals["temperature"], law=P(kp=1.0)
    )
    controller.regulate(30.0)
    return controller


def test_gpio_on_and_off_are_refused_while_a_controller_drives_the_line(rig: Rig) -> None:
    chip = FakeGpio()
    fan = GpioLine("fan", chip, 4)
    rig.add_device(fan)
    assert GpioLine.commands["on"].writes == ("on",) == GpioLine.commands["off"].writes
    controller = _regulate(rig, fan.signals["on"])
    for command in ("on", "off"):
        with pytest.raises(ConflictError, match="driven by controller"):
            rig.run_command(fan, command)
    controller.manual()
    rig.run_command(fan, "on")
    assert chip.get(4) is True


def test_pwm_off_is_refused_while_a_controller_drives_it(rig: Rig) -> None:
    heater = PwmChannel("heater", FakePwm(), 0, frequency_hz=1000)
    rig.add_device(heater)
    controller = _regulate(rig, heater.signals["drive"])
    with pytest.raises(ConflictError, match="driven by controller"):
        rig.run_command(heater, "off")
    rig.run_command(heater, "set_frequency", {"frequency_hz": 500.0}), "moves no demand"
    controller.manual()
    rig.run_command(heater, "off")
    assert heater.signals["drive"].value == 0.0


def test_dispense_declares_the_child_it_moves() -> None:
    assert DosingPump.commands["dispense"].writes == ("pump",)
    assert DosingPump.commands["stop"].writes == (), "a stop is never refused"
