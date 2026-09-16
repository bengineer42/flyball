"""Commands run by the rig: linked arguments, clamping, ownership, mode, readbacks, `last`."""

from __future__ import annotations

from typing import Annotated

import pytest

from flyball.control.laws import P
from flyball.core.device import Committable, Demand, Namespace, Output, Readable, command
from flyball.core.errors import ConflictError, NotFoundError
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, Role, Sample, Section
from flyball.core.units.si import Celsius, Percent
from flyball.core.utils import Labelled
from flyball.runtime.rig import Rig
from flyball.sim import SteppedClock

TEMP = Quantity("temperature", Celsius)
DUTY = Quantity("duty", Percent)
A, B = Section("a", "Bank A", axis="bank"), Section("b", "Bank B", axis="bank")


class Mode(Labelled):
    AUTO = "auto", "Automatic"
    HAND = "hand", "By hand"
    OFF = "off", "Off"


class Heater(Committable):
    """Two banks driven together by hand, or one demand a controller drives."""

    banks = Namespace("banks", "Banks")
    max_duty = Output("max_duty", "Max duty", DUTY, access=Access.R, initial=80.0)
    power = Demand("power", "Power", DUTY, limits=(0.0, 100.0))
    a = banks.demand(A, "Bank A duty", DUTY, limits=(0.0, max_duty))
    b = banks.demand(B, "Bank B duty", DUTY, limits=(0.0, max_duty))
    mode = Output("mode", "Mode", vtype=Mode, initial=Mode.AUTO)
    writes: list[tuple[str, float]]

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.writes = []

    def commit(self, time_ns: int) -> None:
        if (power := self.power.pending) is not None:
            self.writes.append(("power", power))
            if self.mode.value is not Mode.AUTO:
                self.mode.push(Mode.AUTO, time_ns)

    @command(mode=Mode.HAND)
    def set_banks(self, a: Annotated[float, a], b: Annotated[float, b]) -> float:
        """Drive each bank at a duty."""
        self.writes.append(("a", a))
        self.writes.append(("b", b))
        return a + b

    @command(mode=Mode.OFF, interrupts=True)
    def off(self) -> None:
        """Both banks off; a controller on `power` goes to manual."""
        self.a.push(0.0)
        self.b.push(0.0)

    @command
    def reset(self) -> str:
        """A maintenance command: no mode, no ownership check."""
        return "reset"


class Probe(Readable):
    temperature = Output("temperature", "Temperature", TEMP)

    def read(self, time_ns, node=None):
        yield self.sample(time_ns, temperature=20.0)


@pytest.fixture
def rig() -> Rig:
    rig = Rig()
    rig.clock = SteppedClock(0)
    return rig


@pytest.fixture
def heater(rig: Rig) -> Heater:
    device = Heater("heater")
    rig.add_device(device)
    return device


class TestStructure:
    def test_tree_roles_sections_and_synthesised_setters(self, heater: Heater) -> None:
        assert list(heater.signals) == [
            "conditions",
            "max_duty",
            "power",
            "mode",
            "banks.a",
            "banks.b",
            "last.set_banks",
            "last.off",
            "last.reset",
        ]
        assert heater.a.role is Role.DEMAND and heater.a.access is Access.RPW
        assert heater.a.tags == {"bank": "a"}
        assert heater.a.limits == (0.0, 80.0), "the max_duty output's value bounds it"
        assert set(Heater.commands) == {"set_banks", "off", "reset", "set_power"}
        assert Heater.commands["set_power"].demand_of == "power"
        assert Heater.commands["set_banks"].params["a"].link == "banks.a"
        assert Heater.readable is False and Heater.writable is True
        assert heater.mode.value is Mode.AUTO, "initial values are known once added"


class TestRun:
    def test_a_command_sets_the_mode_pushes_readbacks_and_records_last(
        self, rig: Rig, heater: Heater
    ) -> None:
        rig.clock.advance(1)
        assert rig.run_command(heater, "set_banks", {"a": 30.0, "b": 40.0}) == pytest.approx(70.0)
        assert heater.mode.value is Mode.HAND
        assert heater.a.value == pytest.approx(30.0) and heater.b.value == pytest.approx(40.0)
        last = heater.signals["last.set_banks"].value
        assert last["args"] == {"a": 30.0, "b": 40.0}

    def test_a_missing_linked_argument_takes_the_current_value(
        self, rig: Rig, heater: Heater
    ) -> None:
        rig.run_command(heater, "set_banks", {"a": 30.0, "b": 40.0})
        rig.run_command(heater, "set_banks", {"b": 10.0})
        assert heater.writes[-2:] == [("a", 30.0), ("b", 10.0)]

    def test_a_linked_argument_is_clamped_to_the_signal_s_limits(
        self, rig: Rig, heater: Heater
    ) -> None:
        rig.run_command(heater, "set_banks", {"a": 500.0, "b": -1.0})
        assert heater.writes[-2:] == [("a", 80.0), ("b", 0.0)]

    def test_a_synthesised_setter_is_a_demand(self, rig: Rig, heater: Heater) -> None:
        states = rig.run_command(heater, "set_power", {"value": 55.0})
        assert states[heater.power].value == pytest.approx(55.0)
        assert heater.writes == [("power", 55.0)]
        assert heater.power.value == pytest.approx(55.0), "the rig pushes the committed value"

    def test_a_driver_s_own_readback_is_not_overwritten(self, rig: Rig, heater: Heater) -> None:
        rig.run_command(heater, "off")
        assert heater.a.value == 0.0 and heater.mode.value is Mode.OFF

    def test_an_unknown_command_is_not_found(self, rig: Rig, heater: Heater) -> None:
        with pytest.raises(NotFoundError):
            rig.run_command(heater, "explode")

    def test_a_driven_device_refuses_commands_unless_they_interrupt(
        self, rig: Rig, heater: Heater
    ) -> None:
        probe = Probe("probe")
        rig.add_device(probe)
        controller = rig.attach_controller(heater.power, probe.temperature, law=P(kp=1.0))
        controller.regulate(30.0)
        with pytest.raises(ConflictError, match="driven by controller"):
            rig.run_command(heater, "set_banks", {"a": 1.0, "b": 1.0})
        rig.run_command(heater, "reset"), "no mode, no link: runs regardless"
        rig.run_command(heater, "off")
        assert not controller.mode.active() and rig.recent[-1].kind == "interrupted"
        rig.run_command(heater, "set_banks", {"a": 1.0, "b": 1.0}), "manual now: allowed"

    def test_a_demand_puts_the_device_back_in_auto_from_within_commit(
        self, rig: Rig, heater: Heater
    ) -> None:
        rig.run_command(heater, "set_banks", {"a": 1.0, "b": 1.0})
        assert heater.mode.value is Mode.HAND
        rig.demand(heater.root, {"power": 10.0})
        assert heater.mode.value is Mode.AUTO, "pushed inside commit, delivered after it"
        assert rig.router.sample(heater.root) is not None


def test_a_reading_on_an_input_commits_the_device_that_follows_it(rig: Rig) -> None:
    class Follower(Committable):
        source = Demand("source", "Source", TEMP)
        commits: list[float]

        def __init__(self, name: str) -> None:
            super().__init__(name)
            self.commits = []

        def commit(self, time_ns: int) -> None:
            self.commits.append(self.bound["probe"].value)

    probe, follower = Probe("probe"), Follower("follower")
    rig.add_device(probe)
    rig.add_device(follower)
    rig.bind_inputs(follower, {"probe": "probe.temperature"})
    rig.on_samples([Sample(probe.root, 1, {probe.temperature: 21.5})])
    assert follower.commits == [21.5]
