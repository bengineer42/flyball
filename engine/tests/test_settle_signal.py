"""`settle` on a signal: a criterion judged on each reading of a signal the step follows.

The controller form (`settle: {controllers, within}`) is tested in `test_programmer.py`.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from conftest import TestClient
from flyball.foundation.device import (
    Node,
    OnNoValue,
    Readable,
    Readout,
    Reason,
    Sample,
    invalid,
    not_applicable,
    railed,
    stale,
)
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Gram
from flyball.foundation.time import Duration
from flyball.interfaces.server import create_app, set_programmer, set_rig
from flyball.interfaces.server.dialect import Dialect, StepError, commands_from_yaml, step_schema
from flyball.model.catalog import get_catalog
from flyball.rig import Rig
from flyball.sequencing import CriterionMet, Program, Programmer, Settle

MASS = Quantity("mass", Gram)


class Scale(Readable):
    """A weight in a cup: a signal no controller regulates."""

    mass = Readout("mass", "Mass", MASS)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        yield self.sample(time_ns, mass=0.0)


@pytest.fixture
def scale(rig: Rig, fresh) -> Scale:
    device = Scale(fresh("scale"))
    rig.add_device(device)
    return device


def _push(rig: Rig, scale: Scale, *values) -> None:
    for value in values:
        signal = scale.signals["mass"]
        rig.on_samples([Sample(signal.node, rig.clock.now_ns(), {signal: value})])


def _start(rig: Rig, step: Settle) -> Programmer:
    programmer = Programmer(rig)
    programmer.start(Program([step], name="settle"))
    return programmer


def _address(scale: Scale) -> str:
    return f"{scale.name}.mass"


class TestTheTest:
    @pytest.mark.parametrize(
        ("test", "misses", "meets"),
        [
            ({"above": 36}, 36.0, 36.5),
            ({"below": 5}, 5.0, 4.5),
            ({"near": 18, "within": 0.5}, 18.6, 17.5),
        ],
    )
    def test_above_below_and_near(self, rig, scale, test, misses, meets):
        programmer = _start(rig, Settle(signal=_address(scale), count=1, **test))
        assert programmer.running and programmer.state.type == "settle"
        _push(rig, scale, misses)
        assert programmer.running, f"{misses} does not meet {test}"
        _push(rig, scale, meets)
        programmer.join(2)
        assert programmer.running is False and rig.recent[-1].code == "succeeded"

    def test_count_is_readings_running_and_a_miss_resets_it(self, rig, scale):
        programmer = _start(rig, Settle(signal=_address(scale), above=36, count=3))
        _push(rig, scale, 37.0, 38.0, 35.0, 37.0, 38.0)
        assert programmer.running, "the miss reset the count: two running, not three"
        _push(rig, scale, 39.0)
        programmer.join(2)
        assert programmer.running is False

    def test_the_reading_there_at_the_start_is_not_judged(self, rig, scale):
        _push(rig, scale, 40.0)
        programmer = _start(rig, Settle(signal=_address(scale), above=36, count=1))
        assert programmer.running, "a wait for readings from now"
        _push(rig, scale, 40.0)
        programmer.join(2)
        assert programmer.running is False

    def test_an_activity_named_for_the_signal_and_its_test(self, rig, scale):
        programmer = _start(rig, Settle(signal=_address(scale), below=60, count=2))
        state = rig.triggers.states()[f"settle:{_address(scale)}"]
        assert (
            state.prompt is False and state.message == f"{_address(scale)} below 60 for 2 readings"
        )
        programmer.cancel()


class TestFromStart:
    def test_the_number_is_added_to_the_value_at_the_start(self, rig, scale):
        _push(rig, scale, 10.0)
        programmer = _start(rig, Settle(signal=_address(scale), above=36, from_start=True, count=1))
        activity = programmer._activity
        assert isinstance(activity, CriterionMet) and activity.base == 10.0
        _push(rig, scale, 40.0)
        assert programmer.running, "40 is 30 more than 10"
        _push(rig, scale, 46.5)
        programmer.join(2)
        assert programmer.running is False

    def test_with_no_value_at_the_start_the_first_reading_is_the_base(self, rig, scale):
        _push(rig, scale, railed(0.0, "low"))  # at a limit: no base from it
        programmer = _start(rig, Settle(signal=_address(scale), above=36, from_start=True, count=1))
        activity = programmer._activity
        assert isinstance(activity, CriterionMet) and activity.base is None
        _push(rig, scale, 50.0)
        assert programmer.running and activity.base == 50.0, "the base, not judged itself"
        _push(rig, scale, 80.0)
        assert programmer.running
        _push(rig, scale, 86.5)
        programmer.join(2)
        assert programmer.running is False


class TestNoValue:
    def test_by_default_no_value_is_not_met_and_resets_the_count(self, rig, scale):
        programmer = _start(rig, Settle(signal=_address(scale), below=60, count=2))
        _push(rig, scale, 50.0, invalid("crc"), 50.0)
        assert programmer.running
        _push(rig, scale, stale(Reason.SILENT), 50.0)
        assert programmer.running
        _push(rig, scale, 50.0)
        programmer.join(2)
        assert programmer.running is False

    def test_fire_counts_a_fault_as_met(self, rig, scale):
        programmer = _start(
            rig,
            Settle(signal=_address(scale), below=60, count=2, on_no_value=OnNoValue.FIRE),
        )
        _push(rig, scale, invalid("crc"), stale(Reason.DEVICE_OFFLINE))
        programmer.join(2)
        assert programmer.running is False and rig.recent[-1].code == "succeeded"

    def test_not_applicable_never_meets_it_even_under_fire(self, rig, scale):
        programmer = _start(
            rig,
            Settle(signal=_address(scale), below=60, count=1, on_no_value=OnNoValue.FIRE),
        )
        _push(rig, scale, not_applicable("empty"), not_applicable("empty"))
        assert programmer.running
        programmer.cancel()

    def test_a_reading_at_a_limit_resets_the_count(self, rig, scale):
        programmer = _start(rig, Settle(signal=_address(scale), above=36, count=2))
        _push(rig, scale, 40.0, railed(1000.0, "high"), 40.0)
        assert programmer.running, "the value at a limit may lie anywhere beyond it"
        _push(rig, scale, 40.0)
        programmer.join(2)
        assert programmer.running is False


class TestEnding:
    def test_the_binding_is_the_rig_s_while_the_step_runs_and_released_after(self, rig, scale):
        signal = scale.signals["mass"]
        programmer = _start(rig, Settle(signal=_address(scale), above=36, count=1))
        (binding,) = rig.consumers(signal)
        assert binding.where == "program.inputs.settle" and binding.signal is signal
        _push(rig, scale, 40.0)
        programmer.join(2)
        assert rig.consumers(signal) == [] and not binding.bound

    def test_a_cancel_releases_it(self, rig, scale):
        signal = scale.signals["mass"]
        programmer = _start(rig, Settle(signal=_address(scale), above=36, count=1))
        assert rig.consumers(signal)
        programmer.cancel()
        assert programmer.running is False and rig.consumers(signal) == []
        assert rig.recent[-1].code == "cancelled"

    def test_a_program_ended_before_the_wait_releases_it(self, rig, scale):
        """Ended while the step applied: the activity is never attached, so `release` unbinds."""
        signal = scale.signals["mass"]
        programmer = Programmer(rig)

        @dataclass(frozen=True)
        class EndedWhileApplying(Settle, type="settle"):
            def run(self, rig, operator=None):
                activity = super().run(rig, operator)
                programmer.cancel()
                return activity

        programmer.start(Program([EndedWhileApplying(signal=_address(scale), above=36)]))
        programmer.join(2)
        assert programmer.running is False and rig.consumers(signal) == []

    def test_a_timeout_fails_the_program_as_the_controller_form_does(self, rig, scale):
        signal = scale.signals["mass"]
        programmer = _start(
            rig, Settle(signal=_address(scale), above=36, count=1, timeout=Duration(5))
        )
        programmer.join(2)  # the stepped clock steps past the timeout at once
        assert programmer.running is False and rig.consumers(signal) == []
        codes = [e.code for e in rig.recent]
        assert codes[-2:] == ["step_timed_out", "failed"]
        assert programmer.state.failed and "gave up after 5.0 s" in (programmer.state.error or "")

    def test_an_unknown_signal_or_a_namespace_fails_at_the_start(self, rig, scale):
        with pytest.raises(LookupError):
            _start(rig, Settle(signal=f"{scale.name}.nothing", above=1))
        with pytest.raises(ValueError, match="is a namespace, not a signal"):
            _start(rig, Settle(signal=scale.name, above=1))
        assert rig.consumers(scale.signals["mass"]) == []

    def test_missing_names_what_the_rig_lacks(self, rig, scale):
        assert Settle(signal=_address(scale), above=1).missing(rig) == []
        (why,) = Settle(signal=f"{scale.name}.nothing", above=1).missing(rig)
        assert "nothing" in why
        assert Settle(signal=scale.name, above=1).missing(rig) == [
            f"'{scale.name}' is a namespace, not a signal"
        ]


class TestWriting:
    @pytest.fixture
    def dialect(self) -> Dialect:
        return Dialect(steps=dict(get_catalog().steps.items()))

    def test_a_program_file_writes_the_criterion_flat(self, dialect):
        program = commands_from_yaml(
            "steps:\n"
            "  - settle: {signal: scale.mass, above: 36, from_start: true, count: 5,\n"
            "             timeout: {minutes: 2}, on_no_value: fire}\n"
            "  - settle: {signal: furnace.sample, near: 700, within: 5}\n"
            "  - settle: {controllers: [heaters.heater1], within: 3}\n"
            "  - settle: heaters.heater2\n",
            dialect,
        )
        relative, near, controllers, shorthand = program
        assert isinstance(relative, Settle) and relative.criterion() is not None
        assert relative.criterion().describe() == "scale.mass above its start +36"
        assert relative.on_no_value is OnNoValue.FIRE and relative.count == 5
        assert relative.timeout == Duration(120)
        assert near.criterion() is not None and near.criterion().within == 5
        assert controllers.criterion() is None and controllers.within == 3
        assert shorthand.controllers == "heaters.heater2" and shorthand.within is None

    @pytest.mark.parametrize(
        ("body", "why"),
        [
            ("{controllers: [h.a], signal: s.t, above: 1}", "not both"),
            ("{above: 1}", "need a `signal:`"),
            ("{controllers: h.a, from_start: true}", "need a `signal:`"),
            ("{signal: s.t}", "found none"),
            ("{signal: s.t, above: 2, below: 1}", "is not below"),
            ("{signal: s.t, near: 1}", "`near` needs `within`"),
            ("{signal: s.t, above: 1, within: 2}", "`within` goes with `near`"),
        ],
    )
    def test_a_mixed_or_incomplete_form_is_refused_naming_the_step(self, dialect, body, why):
        with pytest.raises(StepError, match=f"step 1: .*{why}"):
            commands_from_yaml(f"steps:\n  - wait: 1\n  - settle: {body}\n", dialect)

    def test_the_schema_offers_both_forms(self, dialect):
        (branch,) = [b for b in step_schema(dialect)["oneOf"] if "settle" in b["properties"]]
        form = branch["properties"]["settle"]["anyOf"][1]["properties"]
        assert {"controllers", "within", "count", "timeout", "message"} <= form.keys()
        assert {"signal", "above", "below", "near", "from_start", "on_no_value"} <= form.keys()

    def test_the_command_route_refuses_a_mixed_form_with_422(self, rig, scale):
        set_rig(rig)
        set_programmer(Programmer(rig))
        client = TestClient(create_app())
        response = client.post(
            "/api/programs/command",
            json={"type": "settle", "controllers": "h.a", "signal": _address(scale), "above": 1},
        )
        assert response.status_code == 422 and "not both" in response.json()["detail"]
        response = client.post(
            "/api/programs/command",
            json={"type": "settle", "signal": _address(scale), "above": 36, "count": 1},
        )
        assert response.status_code == 200 and response.json()["type"] == "settle"
        _push(rig, scale, 40.0)
        client.post("/api/programs/cancel")
        set_programmer(None)
        set_rig(None)
