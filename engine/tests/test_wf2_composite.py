"""Composite devices, Phase 0: `writes=`, interrupting only after success, the RP refusal.

D17 items 3-4 and the refusal of a write to a readback demand.
"""

from __future__ import annotations

from typing import Annotated

import pytest
from flyball_sim import SteppedClock

from conftest import TestClient
from flyball.control.laws import P
from flyball.foundation.device import (
    Access,
    Committable,
    Demand,
    Readable,
    Readout,
    command,
)
from flyball.foundation.errors import ConflictError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Percent
from flyball.rig import Interrupted, Rig

TEMP = Quantity("temperature", Celsius)
DUTY = Quantity("duty", Percent)


class Mixer(Committable):
    """`power` a controller drives; `duty` a readback only `set_duty` moves."""

    power = Demand("power", "Power", DUTY, limits=(0.0, 100.0))
    duty = Demand("duty", "Duty", DUTY, limits=(0.0, 100.0), access=Access.RP)
    trim = Demand("trim", "Trim", DUTY, limits=(0.0, 100.0), access=Access.RP)

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.fail = False
        self.ran: list[str] = []

    def commit(self, time_ns: int) -> None:
        pass

    @command(interrupts=True)
    def set_duty(self, duty: Annotated[float, duty]) -> None:
        """Drive the duty by hand; may fail."""
        if self.fail:
            raise RuntimeError("the bus is down")
        self.ran.append("set_duty")

    @command(writes=(power,))
    def off(self) -> None:
        """Switch off, whatever was demanded: moves `power` though no argument says so."""
        self.ran.append("off")

    @command(writes=("trim",))
    def zero_trim(self) -> None:
        """Moves `trim`, declared by path; refused like `off`."""
        self.trim.push(0.0)

    @command
    def reset(self) -> str:
        """Maintenance: drives nothing."""
        return "reset"


class Probe(Readable):
    temperature = Readout("temperature", "Temperature", TEMP)

    def read(self, time_ns, node=None):
        yield self.sample(time_ns, temperature=20.0)


@pytest.fixture
def rig() -> Rig:
    rig = Rig()
    rig.clock = SteppedClock(0)
    return rig


@pytest.fixture
def mixer(rig: Rig) -> Mixer:
    device = Mixer("mixer")
    rig.add_device(device)
    return device


@pytest.fixture
def controller(rig: Rig, mixer: Mixer):
    probe = Probe("probe")
    rig.add_device(probe)
    controller = rig.attach_controller(mixer.power, probe.temperature, law=P(kp=1.0))
    controller.regulate(30.0)
    return controller


class TestWrites:
    def test_writes_is_resolved_to_paths_at_class_definition(self) -> None:
        assert Mixer.commands["off"].writes == ("power",)
        assert Mixer.commands["zero_trim"].writes == ("trim",)
        assert Mixer.commands["reset"].writes == ()

    def test_a_command_that_declares_writes_is_refused_while_a_controller_drives(
        self, rig: Rig, mixer: Mixer, controller
    ) -> None:
        for name in ("off", "zero_trim"):
            with pytest.raises(ConflictError, match="driven by controller"):
                rig.run_command(mixer, name)
        assert mixer.ran == [] and controller.mode.active()
        assert rig.run_command(mixer, "reset") == "reset", "drives nothing: runs regardless"

    def test_in_manual_a_writes_command_runs(self, rig: Rig, mixer: Mixer, controller) -> None:
        controller.manual()
        rig.run_command(mixer, "off")
        assert mixer.ran == ["off"]

    def test_a_long_command_cannot_interrupt(self) -> None:
        with pytest.raises(TypeError, match="long command cannot interrupt"):

            class Bad(Committable):
                def commit(self, time_ns: int) -> None:
                    pass

                @command(long=True, interrupts=True)
                def dose(self) -> None:
                    """Waits."""


class TestInterruptAfterSuccess:
    def test_the_response_lists_whom_it_interrupted(
        self, rig: Rig, mixer: Mixer, controller
    ) -> None:
        ran = rig.invoke(mixer, "set_duty", {"duty": 40.0})
        assert ran.interrupted == (Interrupted(controller.name, "regulating"),)
        assert not controller.mode.active()
        event = rig.recent[-1]
        assert event.code == "interrupted" and event.details["was"] == "regulating"
        assert rig.invoke(mixer, "set_duty", {"duty": 41.0}).interrupted == (), "none left"

    def test_a_command_that_fails_leaves_the_controller_regulating(
        self, rig: Rig, mixer: Mixer, controller
    ) -> None:
        mixer.fail = True
        with pytest.raises(RuntimeError, match="bus is down"):
            rig.run_command(mixer, "set_duty", {"duty": 40.0})
        assert controller.mode.active(), "put in manual only once the method succeeded"
        assert all(e.code != "interrupted" for e in rig.recent)

    def test_http_lists_interrupted(self, rig: Rig, mixer: Mixer, controller) -> None:
        from flyball.interfaces.server import create_app, set_rig

        set_rig(rig)
        try:
            with TestClient(create_app()) as client:
                self._check_http(client, controller)
        finally:
            set_rig(None)

    @staticmethod
    def _check_http(client, controller) -> None:
        r = client.post("/api/devices/mixer/commands/set_duty", json={"duty": 40.0})
        assert r.status_code == 200, r.text
        assert r.json() == {
            "result": None,
            "interrupted": [{"controller": controller.name, "was": "regulating"}],
        }
        schema = client.get("/api/devices/mixer/schema").json()
        assert schema["commands"]["off"]["writes"] == ["power"]


class TestReadbackRefusal:
    def test_a_write_to_a_readback_demand_names_the_command_that_moves_it(
        self, rig: Rig, mixer: Mixer
    ) -> None:
        with pytest.raises(ConflictError) as refused:
            rig.write(mixer.root, {"duty": 10.0})
        message = str(refused.value)
        assert "moved by the command 'set_duty'" in message
        assert "puts a regulating controller in manual" in message

    def test_a_writes_declaration_counts_as_moving_it(self, rig: Rig, mixer: Mixer) -> None:
        with pytest.raises(ConflictError, match="moved by the command 'zero_trim'$"):
            rig.write(mixer.root, {"trim": 10.0})
