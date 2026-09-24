"""Commands run by the rig: linked arguments, clamping, ownership, mode, readbacks, `last`."""

from __future__ import annotations

from typing import Annotated

import pytest
from flyball_sim import SteppedClock

from flyball.control.laws import P
from flyball.foundation.device import (
    Access,
    Committable,
    Demand,
    Namespace,
    Readable,
    Readout,
    Role,
    Sample,
    command,
)
from flyball.foundation.errors import ConflictError, NotFoundError
from flyball.foundation.primitives import Labelled
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Percent
from flyball.rig import Rig

TEMP = Quantity("temperature", Celsius)
DUTY = Quantity("duty", Percent)


class Mode(Labelled):
    AUTO = "auto", "Automatic"
    HAND = "hand", "By hand"
    OFF = "off", "Off"


class Heater(Committable):
    """Two banks driven together by hand, or one demand a controller drives."""

    banks = Namespace("banks", "Banks")
    max_duty = Readout("max_duty", "Max duty", DUTY, access=Access.R, initial=80.0)
    power = Demand("power", "Power", DUTY, limits=(0.0, 100.0))
    a = banks.demand("a", "Bank A duty", DUTY, limits=(0.0, max_duty), tags={"bank": "a"})
    b = banks.demand("b", "Bank B duty", DUTY, limits=(0.0, max_duty), tags={"bank": "b"})
    mode = Readout("mode", "Mode", vtype=Mode, initial=Mode.AUTO)
    writes: list[tuple[str, float]]

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.writes = []

    def commit(self, time_ns: int) -> None:
        if (power := self.power.staged) is not None:
            self.writes.append(("power", power))
            if self.mode.value is not Mode.AUTO:
                self.mode.push(Mode.AUTO, time_ns)

    @command(sets_mode=Mode.HAND)
    def set_banks(self, a: Annotated[float, a], b: Annotated[float, b]) -> float:
        """Drive each bank at a duty."""
        self.writes.append(("a", a))
        self.writes.append(("b", b))
        return a + b

    @command(sets_mode=Mode.OFF, interrupts=True)
    def off(self) -> None:
        """Both banks off; a controller on `power` goes to manual."""
        self.a.push(0.0)
        self.b.push(0.0)

    @command
    def set_a(self, a) -> None:  # noqa: ANN001  linked by name: the demand's type is the argument's
        """Bank A alone, no annotation."""
        self.writes.append(("a", a))

    @command
    def reset(self) -> str:
        """A maintenance command: no mode, no ownership check."""
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
def heater(rig: Rig) -> Heater:
    device = Heater("heater")
    rig.add_device(device)
    return device


class TestStructure:
    def test_tree_roles_sections_and_synthesised_setters(self, heater: Heater) -> None:
        assert list(heater.signals) == [
            "max_duty",
            "power",
            "mode",
            "banks.a",
            "banks.b",
            "last.set_banks",
            "last.off",
            "last.set_a",
            "last.reset",
        ]
        assert heater.a.role is Role.DEMAND and heater.a.access is Access.RPW
        assert heater.a.tags == {"bank": "a"}
        assert heater.a.limits == (0.0, 80.0), "the max_duty output's value bounds it"
        assert set(Heater.commands) == {"set_banks", "off", "set_a", "reset", "set_power"}
        assert Heater.commands["set_a"].params["a"].link == "banks.a"
        assert Heater.set_a.__annotations__["a"] == Annotated[float, Heater.a]
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
        assert not controller.mode.active() and rig.recent[-1].code == "interrupted"
        rig.run_command(heater, "set_banks", {"a": 1.0, "b": 1.0}), "manual now: allowed"

    def test_a_demand_puts_the_device_back_in_auto_from_within_commit(
        self, rig: Rig, heater: Heater
    ) -> None:
        rig.run_command(heater, "set_banks", {"a": 1.0, "b": 1.0})
        assert heater.mode.value is Mode.HAND
        rig.write(heater.root, {"power": 10.0})
        assert heater.mode.value is Mode.AUTO, "pushed inside commit, delivered after it"
        assert rig.router.sample(heater.root) is not None


def test_none_is_nothing_to_push(rig: Rig, heater: Heater) -> None:
    heater.push(a=1.0, b=None)
    assert heater.a.value == pytest.approx(1.0) and heater.b.reading is None
    heater.push(a=None)
    heater.b.push(None)
    assert heater.a.value == pytest.approx(1.0) and heater.b.reading is None


def test_a_batch_delivers_every_push_inside_it_as_one_sample(rig: Rig, heater: Heater) -> None:
    rig.clock.advance(5)
    with heater.batch():
        heater.a.push(1.0)
        heater.b.push(2.0)
        heater.mode.push(Mode.HAND)
        assert heater.a.reading is None, "not delivered until the batch closes"
    sample = rig.router.sample(heater.root)
    assert sample is not None and sample.values == {
        heater.a: 1.0,
        heater.b: 2.0,
        heater.mode: Mode.HAND,
    }
    assert heater.a.reading is not None and heater.a.reading.time_ns == heater.b.reading.time_ns


def test_recording_declares_a_limit_that_follows_a_signal_as_its_number(
    rig: Rig, heater: Heater, tmp_path
) -> None:
    from flyball.record import SqliteStore

    store = SqliteStore(tmp_path / "rig.sqlite")
    rig.start_recording(store)  # `banks.a` has limits (0, max_duty): declared as (0, 80)
    rig.stop_recording()
    session = store.sessions()[0]
    limits = {s.address: s.limits for s in store.signals(session.id)}
    assert tuple(limits["heater.banks.a"]) == (0.0, 80.0)
    store.close()


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


def test_a_computed_tree_s_demands_get_setters_on_the_instance(rig: Rig) -> None:
    from flyball.foundation.device import Access, SignalSpec

    class Generic(Committable):
        def __init__(self, name: str, ports: list[str]) -> None:
            super().__init__(name)
            self.bind([
                SignalSpec(name=p, quantity=DUTY, access=Access.RPW, role=Role.DEMAND)
                for p in ports
            ])

    device = Generic("g", ["a", "b"])
    rig.add_device(device)
    assert Generic.commands == {}, "the class knows nothing of a tree built per instance"
    assert {t: c.demand_of for t, c in device.commands.items()} == {"set_a": "a", "set_b": "b"}
    states = rig.run_command(device, "set_a", {"value": 3.0})
    assert states[device.signals["a"]].value == pytest.approx(3.0)


def test_the_samples_stream_merges_a_node_s_pushes_within_a_flush(rig: Rig, heater: Heater) -> None:
    with rig.samples.watch():
        rig.clock.advance(1)
        heater.push(a=1.0)
        heater.push(b=2.0)  # a second sample on the same node before any reader flushed
        _, changed = rig.samples.changed_since(0)
    (sample,) = changed.values()
    assert sample.values == {heater.a: 1.0, heater.b: 2.0}, (
        "the newest of every signal, not the last push alone"
    )
