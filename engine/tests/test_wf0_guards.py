"""Robustness guards: failures kept to what failed, and numbers the wire can carry.

A failing commit, a controller feeding back on itself, a demand no driver
read, a writer that outlives a bad report, and non-finite numbers on the wire.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Iterator

import pytest

from conftest import TestClient
from flyball.control.laws import P
from flyball.foundation.device import (
    Committable,
    Demand,
    Kind,
    Level,
    Node,
    Output,
    Readable,
    Sample,
    Scope,
    WriteState,
)
from flyball.interfaces.server import create_app, set_rig
from test_rig import FakeRecorder, FakeStore, recorder_module  # noqa: F401  a fixture
from test_rig_devices import POWER, TEMP, Furnace
from test_server import Daq


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

# region 2. A controller sourced from its own target's device


class Looped(Readable, Committable):
    """A zone and a heater on one device; every commit reads the zone back and pushes it."""

    zone = Output("zone", "Zone", TEMP)
    heater = Demand("heater", "Heater", POWER, limits=(0.0, 1000.0))

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.commits = 0

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        yield self.sample(time_ns, zone=20.0)

    def commit(self, time_ns: int) -> None:
        self.commits += 1
        super().commit(time_ns)
        if self.commits < 200:  # the test's own backstop: a real loop never stops
            self.push(time_ns, zone=20.0 + self.commits)


def test_a_controller_whose_commit_pushes_its_own_source_steps_once(rig, fresh):
    looped = Looped(fresh("looped"))
    rig.add_device(looped)
    controller = rig.attach_controller(
        looped.signals["heater"], looped.signals["zone"], law=P(kp=1.0)
    )
    controller.regulate(50.0)
    looped.commits = 0
    rig.on_samples(list(looped.read(rig.clock.now_ns())))
    assert looped.commits == 1, "its readback does not step it again in the same delivery"
    assert rig.latest[looped.signals["zone"]].value == 21.0, "the readback still landed"
    rig.on_samples(list(looped.read(rig.clock.now_ns())))
    assert looped.commits == 2, "the next delivery steps it again"


def test_a_controller_on_its_own_target_s_device_is_attached(rig, furnace):
    """The ordinary case -- one instrument's PV and output -- is allowed and does not loop."""
    controller = rig.attach_controller(
        furnace.signals["heater1"], furnace.signals["zone1"], law=P(kp=1.0)
    )
    controller.regulate(30.0)
    furnace.commits = 0
    _deliver(rig, furnace)
    assert furnace.commits == 1


# endregion

# region 3. A demand the driver never read


class Picky(Committable):
    """Reads only `a` in `commit`, as a blender reads its target and not its flows."""

    a = Demand("a", "A", POWER, limits=(0.0, 100.0))
    b = Demand("b", "B", POWER, limits=(0.0, 100.0))

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.seen: list[float] = []

    def commit(self, time_ns: int) -> None:
        if (value := self.signals["a"].pending) is not None:
            self.seen.append(value)


def test_a_demand_the_driver_did_not_read_is_reported_not_echoed(rig, fresh):
    picky = Picky(fresh("picky"))
    rig.add_device(picky)
    a, b = picky.signals["a"], picky.signals["b"]
    states = rig.demand(picky.root, {b: 5.0})
    (event,) = _events(rig, Kind.DEMAND_IGNORED)
    assert event.scope == Scope.DEVICE and event.subject == picky.name
    assert event.level is Level.WARNING and event.details["signal"] == b.address
    assert rig.latest.get(b) is None, "not echoed as a readback"
    assert states[b] == WriteState(value=None, requested=5.0)
    assert picky.pending == {}
    rig.demand(picky.root, {b: 6.0})
    assert len(_events(rig, Kind.DEMAND_IGNORED)) == 1, "one event while it goes unread"

    rig.demand(picky.root, {a: 7.0})
    assert picky.seen == [7.0] and rig.latest[a].value == 7.0, "a read demand is echoed"
    assert len(_events(rig, Kind.DEMAND_IGNORED)) == 1


def test_a_driver_that_writes_pending_through_raises_nothing(rig, furnace):
    rig.demand(furnace.root, {"heater1": 100.0, "heater2": 200.0})
    assert furnace.inputs == {"heater1": 100.0, "heater2": 200.0}
    assert rig.latest[furnace.signals["heater1"]].value == 100.0
    assert not _events(rig, Kind.DEMAND_IGNORED)


# endregion

# region 4. The writer outlives a report that raises


class Blocking(Furnace):
    blocking = True


def _until(condition, timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)


def test_a_raise_in_written_does_not_kill_the_writer(rig, fresh, monkeypatch):
    device = Blocking(fresh("blocking"))
    rig.add_device(device)
    real = rig.written
    calls = []

    def written(*args):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("a bug downstream")
        real(*args)

    monkeypatch.setattr(rig, "written", written)
    try:
        rig.demand(device.root, {"heater1": 10.0})
        writer = rig._writers[device]
        _until(lambda: writer.failed is not None)
        assert (
            writer.failed.kind == Kind.WRITE_FAILED and "a bug downstream" in writer.failed.message
        )
        assert _events(rig, Kind.WRITE_FAILED)
        assert writer._thread.is_alive()
        rig.demand(device.root, {"heater1": 20.0})
        _until(lambda: len(calls) == 2 and writer.failed is None)
        assert _events(rig, Kind.WRITE_RECOVERED)
        assert rig.latest[device.signals["heater1"]].value == 20.0
    finally:
        rig.close()


# endregion

# region 5. Non-finite floats on the wire


def _strict(text: str) -> object:
    """`JSON.parse`: a bare NaN or Infinity is a syntax error."""

    def refuse(constant: str) -> object:
        raise ValueError(f"{constant} is not JSON")

    return json.loads(text, parse_constant=refuse)


@pytest.fixture
def served(rig) -> Iterator[TestClient]:
    set_rig(rig)
    with TestClient(create_app()) as client:
        yield client
    set_rig(None)


def test_a_nan_controller_state_crosses_as_null(rig, furnace, served):
    controller = rig.attach_controller(
        furnace.signals["heater1"], furnace.signals["zone1"], law=P(kp=1.0)
    )
    controller.correction = math.nan
    controller.expected = math.inf
    with served.websocket_connect("/ws/controllers") as ws:
        frame = _strict(ws.receive_text())
        (out,) = frame["controllers"]  # type: ignore[index]
        assert out["correction"] is None and out["expected"] is None
    response = served.get(f"/api/controllers/{controller.name}")
    assert response.status_code == 200
    body = _strict(response.text)
    assert body["correction"] is None and body["expected"] is None  # type: ignore[index]
    listed = served.get("/api/controllers")
    assert listed.status_code == 200 and _strict(listed.text)[0]["correction"] is None  # type: ignore[index]


def test_a_nan_reading_crosses_as_null_on_the_samples_stream(rig, furnace, served):
    _deliver(rig, furnace, math.nan)
    with served.websocket_connect("/ws/samples") as ws:
        frame = _strict(ws.receive_text())
        entry = next(s for s in frame["samples"] if s["node"] == furnace.name)  # type: ignore[index]
        assert entry["values"]["zone1"] is None


def test_a_nan_in_an_event_crosses_as_null(rig, served):
    rig.event(Level.INFO, Scope.RIG, "x", Kind.RESTORED, "odd", {"value": math.nan})
    with served.websocket_connect("/ws/events") as ws:
        frame = _strict(ws.receive_text())
        assert frame["events"][-1]["details"] == {"value": None}  # type: ignore[index]


# endregion

# region 6. Health on values that are not numbers


@pytest.mark.parametrize("value", [None, math.nan, math.inf, "open"])
def test_health_counts_a_non_number_as_neither_in_nor_out_of_band(rig, fresh, served, value):
    daq = Daq(fresh("daq"))
    rig.add_device(daq)
    zone1, zone2 = daq.signals["zone1"], daq.signals["zone2"]
    rig.on_samples([Sample(daq.root, 1, {zone1: value, zone2: 50.0})])
    response = served.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["alarms"] == {"warn": 0, "alarm": 0, "max_level": 0}
    assert body["ok"] is True


# endregion
