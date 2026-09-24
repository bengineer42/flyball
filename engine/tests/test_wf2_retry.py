"""Signal faults stage 3, reads retry: a failure budget, then `offline` and a retry with backoff."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from flyball_sim import SteppedClock
from pydantic import ValidationError

from conftest import TestClient
from flyball.foundation.device import (
    Access,
    Code,
    DriverConfig,
    Node,
    Readable,
    Role,
    Sample,
    SignalSpec,
    command,
)
from flyball.foundation.device.entry import DeviceEntry, Reads
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import One
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.schemas import RunOut
from flyball.model.catalog import get_catalog
from flyball.rig import Rig
from flyball.rig.polling import ReadPolicy
from flyball.runtime.config import RigConfig, RunnerConfig

COUNT = Quantity("count", One)


class Flaky(Readable):
    """Two namespaced counters; raises while `fail`, and after its first sample while `partial`."""

    TREE = (
        SignalSpec(name="a", quantity=COUNT, access=Access.RP, role=Role.READOUT),
        SignalSpec(name="b", quantity=COUNT, access=Access.RP, role=Role.READOUT),
    )

    def __init__(self, name: str, clock: SteppedClock | None = None) -> None:
        super().__init__(name)
        self.clock = clock
        self.fail = False
        self.partial = False
        self.at: list[float] = []
        """The rig time of every read, in seconds."""
        self.seen: list[int | None] = []
        """What the run said of `reading_since_ns` during each read."""
        self.rig: Rig | None = None

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        if self.clock is not None:
            self.at.append(round(self.clock.monotonic(), 6))
        if self.rig is not None:
            self.seen.append(self.rig.polling.run(self.name).reading_since_ns)
        if self.fail:
            raise OSError("bus timeout")
        yield Sample(self.root, time_ns, {self.signals["a"]: float(len(self.at))})
        if self.partial:
            raise OSError("crc error on b")
        yield Sample(self.root, time_ns, {self.signals["b"]: float(len(self.at))})

    @command
    def reset(self) -> None:
        """Put it right."""
        self.fail = False


@pytest.fixture
def flaky(rig: Rig, clock: SteppedClock, fresh) -> Flaky:
    flaky = Flaky(fresh("flaky"), clock)
    flaky.poll_s = 1.0
    rig.add_device(flaky)
    return flaky


def _edges(rig: Rig, device: Flaky) -> list[tuple[str, str | None]]:
    return [(e.code, e.edge) for e in rig.recent if e.subject == device.name]


class TestBudget:
    def test_reads_below_the_budget_raise_no_condition(self, rig, clock, flaky):
        rig.start_polling(flaky)
        flaky.fail = True
        clock.advance(2.0)
        run = rig.polling.run(flaky.name)
        assert run.consecutive_failures == 2 and run.running is True
        assert rig.conditions.of(flaky) == [] and _edges(rig, flaky) == []
        assert run.next_retry_ns is None, "still on the period"

    def test_the_budget_s_last_failure_holds_offline_and_the_loop_runs_on(self, rig, clock, flaky):
        rig.start_polling(flaky)
        flaky.fail = True
        clock.advance(3.0)
        run = rig.polling.run(flaky.name)
        (offline,) = rig.conditions.of(flaky)
        assert offline.code == Code.OFFLINE and "bus timeout" in offline.message
        assert run.running is True and run.consecutive_failures == 3
        assert run.next_retry_ns == clock.now_ns() + 1_000_000_000, "the first backoff, 1 s"
        assert _edges(rig, flaky) == [("offline", "raised")]

    def test_the_retries_back_off_and_the_last_step_repeats_for_ever(self, rig, clock, flaky):
        rig.start_polling(flaky)
        flaky.fail = True
        clock.advance(400.0)
        assert flaky.at[:3] == [1.0, 2.0, 3.0], "on the period up to the budget"
        assert flaky.at[3:] == [4.0, 6.0, 11.0, 26.0, 86.0, 146.0, 206.0, 266.0, 326.0, 386.0]
        assert _edges(rig, flaky) == [("offline", "raised")], "one edge, however many retries"

    def test_the_first_good_read_clears_offline_and_polling_returns_to_its_period(
        self, rig, clock, flaky
    ):
        rig.start_polling(flaky)
        flaky.fail = True
        clock.advance(6.0)  # offline at 3; retries at 4 and 6
        flaky.fail = False
        clock.advance(10.0)
        assert flaky.at == [1.0, 2.0, 3.0, 4.0, 6.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]
        assert rig.conditions.of(flaky) == []
        assert _edges(rig, flaky) == [("offline", "raised"), ("offline", "cleared")]
        run = rig.polling.run(flaky.name)
        assert run.consecutive_failures == 0 and run.next_retry_ns is None and run.running

    def test_a_good_read_below_the_budget_resets_the_count(self, rig, clock, flaky):
        rig.start_polling(flaky)
        flaky.fail = True
        clock.advance(2.0)
        flaky.fail = False
        clock.advance(1.0)
        flaky.fail = True
        clock.advance(2.0)
        assert rig.polling.run(flaky.name).consecutive_failures == 2
        assert rig.conditions.of(flaky) == []

    def test_give_up_after_stops_the_loop_and_keeps_offline(self, rig, clock, flaky):
        rig.polling.defaults = ReadPolicy(fail_after=1, backoff_s=(5.0,), give_up_after_s=12.0)
        rig.start_polling(flaky)
        flaky.fail = True
        clock.advance(60.0)
        assert flaky.at == [1.0, 6.0, 11.0, 16.0], "offline at 1; 15 s on, it gives up"
        run = rig.polling.run(flaky.name)
        assert run.running is False and run.next_retry_ns is None
        assert [c.code for c in rig.conditions.of(flaky)] == ["offline"]
        assert [e.code for e in rig.recent if e.subject == flaky.name][-1] == Code.GAVE_UP


class TestPartial:
    def test_samples_before_a_raise_are_delivered_and_the_raise_counts(self, rig, clock, flaky):
        rig.start_polling(flaky)
        flaky.partial = True
        clock.advance(1.0)
        assert rig.latest[flaky.signals["a"]].value == 1.0, "the first sample is delivered"
        assert flaky.signals["b"] not in rig.latest
        run = rig.polling.run(flaky.name)
        assert run.consecutive_failures == 1 and run.last_read_ns == clock.now_ns()


class TestRestart:
    def test_restart_clears_nothing_until_a_read_succeeds(self, rig, clock, flaky):
        rig.start_polling(flaky)
        flaky.fail = True
        clock.advance(3.0)
        rig.polling.restart(flaky.name)
        assert [c.code for c in rig.conditions.of(flaky)] == ["offline"], "still offline"
        clock.advance(1.0)  # still broken: no cleared edge, no second raised
        assert _edges(rig, flaky) == [("offline", "raised")]
        flaky.fail = False
        clock.advance(10.0)
        assert _edges(rig, flaky) == [("offline", "raised"), ("offline", "cleared")]

    def test_a_restart_retries_on_the_period_not_the_backoff(self, rig, clock, flaky):
        rig.start_polling(flaky)
        flaky.fail = True
        clock.advance(11.0)  # retries at 4, 6, 11; next at 26
        flaky.fail = False
        rig.polling.restart(flaky.name)
        clock.advance(1.0)
        assert flaky.at[-1] == 12.0 and rig.conditions.of(flaky) == []

    def test_a_command_revives_a_device_that_is_waiting_on_its_backoff(self, rig, clock, flaky):
        rig.start_polling(flaky)
        flaky.fail = True
        clock.advance(11.0)
        assert rig.polling.run(flaky.name).running is True
        flaky.reset()
        assert rig.polling.revive(flaky.name) is True
        clock.advance(1.0)
        assert flaky.at[-1] == 12.0 and rig.conditions.of(flaky) == []


class TestConfig:
    def test_reads_keys_are_validated(self):
        assert Reads(fail_after=5, backoff_s=[0.5, 30], give_up_after_s=None).fail_after == 5
        for bad in (
            {"fail_after": 0},
            {"backoff_s": []},
            {"backoff_s": [1, -2]},
            {"backoff_s": [float("inf")]},
            {"give_up_after_s": 0},
            {"give_up_after_s": float("nan")},
            {"retries": 3},
        ):
            with pytest.raises(ValidationError):
                Reads(**bad)
        with pytest.raises(ValidationError):
            RunnerConfig(reads={"fail_after": -1})
        with pytest.raises(ValidationError):  # per device only
            RunnerConfig(reads={"give_up_after_s": 5})
        assert RunnerConfig().reads.fail_after == 3
        assert RunnerConfig().reads.backoff_s == [1, 2, 5, 15, 60]

    def test_reads_is_an_envelope_key_not_the_driver_s(self):
        entry = DeviceEntry.model_validate({"driver": "x", "reads": {"fail_after": 5}, "zones": 2})
        assert entry.reads == Reads(fail_after=5) and entry.driver_config == {"zones": 2}

    def test_a_device_s_keys_win_over_the_runner_s_which_win_over_the_defaults(self, fresh):
        tag = fresh("flaky")

        class FlakyConfig(DriverConfig[Flaky], type=tag):
            def build(self, name: str, label: str | None = None) -> Flaky:
                return Flaky(name)

        get_catalog().register_device(FlakyConfig)
        config = RigConfig.model_validate({
            "runner": {"reads": {"fail_after": 4, "backoff_s": [2, 10]}},
            "devices": {
                "own": {"driver": tag, "poll_s": 1, "reads": {"backoff_s": [7]}},
                "plain": {"driver": tag, "poll_s": 1},
                "patient": {"driver": tag, "poll_s": 1, "reads": {"give_up_after_s": 600}},
            },
        })
        rig = config.build(clock=SteppedClock(0), start=False)
        try:
            assert rig.polling.policy(rig.devices["own"]) == ReadPolicy(4, (7.0,), None)
            assert rig.polling.policy(rig.devices["plain"]) == ReadPolicy(4, (2.0, 10.0), None)
            assert rig.polling.policy(rig.devices["patient"]) == ReadPolicy(4, (2.0, 10.0), 600)
        finally:
            rig.close()
        bare = Rig()
        assert bare.polling.policy(Flaky("x")) == ReadPolicy(3, (1.0, 2.0, 5.0, 15.0, 60.0), None)

    def test_the_schema_carries_reads(self):
        schema = RigConfig.model_json_schema()
        text = str(schema)
        assert "give_up_after_s" in text and "fail_after" in text and "backoff_s" in text


class TestWire:
    def test_reading_since_ns_is_set_during_a_read_and_none_after(self, rig, clock, flaky):
        flaky.rig = rig
        rig.start_polling(flaky)
        clock.advance(1.0)
        assert flaky.seen == [clock.now_ns()]
        assert rig.polling.run(flaky.name).reading_since_ns is None

    def test_run_out_carries_the_retry_fields(self, rig, clock, flaky):
        rig.start_polling(flaky)
        flaky.fail = True
        clock.advance(3.0)
        out = RunOut.of(rig.polling.run(flaky.name))
        assert out.consecutive_failures == 3 and out.reading_since_ns is None
        assert out.next_retry_ns == clock.now_ns() + 1_000_000_000

    def test_the_device_route_and_the_runs_stream_carry_them(self, rig, clock, flaky):
        rig.start_polling(flaky)
        flaky.fail = True
        clock.advance(3.0)
        set_rig(rig)
        try:
            with TestClient(create_app()) as client:
                run = client.get(f"/api/devices/{flaky.name}").json()["run"]
                assert run["consecutive_failures"] == 3 and run["reading_since_ns"] is None
                assert run["next_retry_ns"] == clock.now_ns() + 1_000_000_000
                with client.websocket_connect("/ws/samples") as ws:
                    frame = ws.receive_json()
                    while "runs" not in frame:
                        frame = ws.receive_json()
                    (streamed,) = [r for r in frame["runs"] if r["name"] == flaky.name]
                    assert streamed["consecutive_failures"] == 3
                    assert streamed["next_retry_ns"] == run["next_retry_ns"]
        finally:
            set_rig(None)


class TestStop:
    """On threads and wall time: a loop waiting on a long backoff stops at once."""

    @pytest.fixture
    def waiting(self, fresh) -> Iterator[tuple[Rig, Flaky]]:
        rig = Rig()
        rig.polling.defaults = ReadPolicy(fail_after=1, backoff_s=(60.0,))
        device = Flaky(fresh("flaky"))
        device.poll_s = 0.02
        device.fail = True
        rig.add_device(device)
        rig.start_polling(device)
        deadline = time.monotonic() + 2.0
        while rig.polling.run(device.name).next_retry_ns is None:
            assert time.monotonic() < deadline, "never went offline"
            time.sleep(0.01)
        yield rig, device
        rig.close()

    def test_removal_stops_the_retry_loop_at_once(self, waiting):
        rig, device = waiting
        loop = rig.polling.periodic[device.name]
        started = time.monotonic()
        rig.remove_device(device.name)
        while loop.running:
            assert time.monotonic() - started < 0.5, "still waiting on its 60 s backoff"
            time.sleep(0.01)
        assert rig.conditions.of(device) == []

    def test_close_stops_the_retry_loop_at_once(self, waiting):
        rig, device = waiting
        loop = rig.polling.periodic[device.name]
        started = time.monotonic()
        rig.close()
        assert time.monotonic() - started < 0.5 and not loop.running
