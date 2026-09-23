"""Robustness guards: failures kept to what failed, and numbers the wire can carry.

A failing commit, a controller feeding back on itself, a demand no driver
read, a writer that outlives a bad report, and non-finite numbers on the wire.
"""

from __future__ import annotations

import pytest

from flyball.control.laws import P
from flyball.foundation.device import (
    Committable,
    Demand,
    Kind,
    Level,
    Sample,
    Scope,
)
from test_rig import FakeRecorder, FakeStore, recorder_module  # noqa: F401  a fixture
from test_rig_devices import POWER, Furnace


class Flaky(Committable):
    """One demand; `commit` raises while `fail` is set, as a bus that went away would."""

    out = Demand("out", "Out", POWER, limits=(0.0, 100.0))

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.fail = False
        self.commits = 0

    def commit(self, time_ns: int) -> None:
        self.commits += 1
        if self.fail:
            raise OSError("bus gone")
        super().commit(time_ns)


class Good(Committable):
    """Counts its commits; nothing else."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.commits = 0

    def commit(self, time_ns: int) -> None:
        self.commits += 1


def _events(rig, kind: Kind) -> list:
    return [e for e in rig.recent if e.kind == kind]


@pytest.fixture
def furnace(rig, fresh) -> Furnace:
    furnace = Furnace(fresh("furnace"))
    rig.add_device(furnace)
    return furnace


def _deliver(rig, furnace: Furnace, value: float = 20.0) -> None:
    zone1 = furnace.signals["zone1"]
    rig.on_samples([Sample(furnace.root, rig.clock.now_ns(), {zone1: value})])


# region 1. A device's commit failure is isolated


class TestCommitFailure:
    @pytest.fixture
    def flaky(self, rig, fresh, furnace) -> Flaky:
        flaky = Flaky(fresh("flaky"))
        rig.add_device(flaky)
        rig.bind_inputs(flaky, {"in": f"{furnace.name}.zone1"})
        return flaky

    @pytest.mark.usefixtures("recorder_module")
    def test_the_rest_of_the_delivery_goes_on(self, rig, clock, fresh, furnace, flaky):
        rig.add_device(good := Good(fresh("good")))
        rig.bind_inputs(good, {"in": f"{furnace.name}.zone1"})
        recorder = rig.start_recording(FakeStore())
        assert isinstance(recorder, FakeRecorder)
        flaky.fail = True
        _deliver(rig, furnace)  # does not raise
        assert good.commits == 1, "the other device still committed"
        assert len(recorder.records) == 1, "the recorder still got the delivery"
        (event,) = _events(rig, Kind.COMMIT_FAILED)
        assert event.scope == Scope.DEVICE and event.subject == flaky.name
        assert event.level is Level.ERROR and "bus gone" in event.message
        assert not _events(rig, Kind.DELIVERY_FAILED)

    def test_one_event_per_outage_then_a_recovery(self, rig, clock, furnace, flaky):
        flaky.fail = True
        for _ in range(3):
            clock.advance(1.0)
            _deliver(rig, furnace)
        assert flaky.commits == 3 and len(_events(rig, Kind.COMMIT_FAILED)) == 1
        (condition,) = [c for name, c in rig.write_conditions() if name == flaky.name]
        assert condition.kind == Kind.COMMIT_FAILED and condition.level is Level.ERROR
        flaky.fail = False
        clock.advance(1.0)
        _deliver(rig, furnace)
        (recovered,) = _events(rig, Kind.COMMIT_RECOVERED)
        assert recovered.subject == flaky.name and recovered.level is Level.INFO
        assert not [c for name, c in rig.write_conditions() if name == flaky.name]
        clock.advance(1.0)
        _deliver(rig, furnace)
        assert len(_events(rig, Kind.COMMIT_RECOVERED)) == 1

    def test_a_failed_demand_is_not_applied_later(self, rig, clock, furnace, flaky):
        out = flaky.signals["out"]
        flaky.fail = True
        with pytest.raises(OSError, match="bus gone"):
            rig.demand(flaky.root, {out: 80.0})  # a manual write still hears the failure
        assert flaky.pending == {}, "the failed demand does not linger"
        flaky.fail = False
        clock.advance(1.0)
        _deliver(rig, furnace)  # an input landing commits again: nothing stale goes out
        assert rig.latest.get(out) is None or rig.latest[out].value != 80.0

    def test_the_controller_hears_the_failed_write(self, rig, clock, furnace, flaky):
        controller = rig.attach_controller(
            flaky.signals["out"], furnace.signals["zone1"], law=P(kp=1.0)
        )
        controller.regulate(30.0)
        _deliver(rig, furnace)
        assert controller.expected is not None, "a good commit reports what was set"
        flaky.fail = True
        clock.advance(1.0)
        _deliver(rig, furnace)
        assert controller.expected is None, "a failed commit set nothing"


# endregion
