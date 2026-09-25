"""Races the rig's lock closes: a stop against a write's checks (L1).

Each test lands the other thread's action exactly between a check and what it guards, by
running it from inside the first thread's check.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from flyball.foundation.device import Code, Sample
from flyball.foundation.errors import ConflictError
from flyball.rig import Rig
from flyball.rig import stopping as stopping_mod
from flyball.rig.stopping import RigStopper
from test_wf3_stop import AGENT, BEN, Oven


@pytest.fixture
def oven(rig: Rig, fresh: Any) -> Oven:
    device = Oven(fresh("oven"))
    rig.add_device(device)
    now = rig.clock.now_ns()
    rig.on_samples([Sample(device.root, now, {device.signals["zone1"]: 20.0})])
    return device


def _between_check_and_commit(rig: Rig, monkeypatch: pytest.MonkeyPatch, act: Any) -> None:
    """Run `act` on another thread once, the first time a write checks a permissive.

    That check comes after the write's latch check and before it takes the rig's lock.
    """
    real = rig._not_permitted
    done: list[bool] = []

    def checking(signal: Any, value: Any) -> Any:
        if not done:
            done.append(True)
            thread = threading.Thread(target=act, daemon=True)
            thread.start()
            thread.join(5.0)
            assert not thread.is_alive()
        return real(signal, value)

    monkeypatch.setattr(rig, "_not_permitted", checking)


class TestAStopAgainstAWrite:
    def test_a_stop_landing_after_the_check_wins(self, rig, oven, monkeypatch):
        _between_check_and_commit(rig, monkeypatch, lambda: RigStopper(rig).stop(BEN, "race"))
        with pytest.raises(ConflictError, match="stopped"):
            rig.write(oven.root, {"h1": 50.0}, actor=AGENT)
        assert ("h1", 50.0) not in oven.writes, "nothing reached the hardware after the stop"
        assert ("h1", 0.0) in oven.writes, "the stop wrote its value"

    def test_a_persons_write_goes_through_the_stop_and_says_so(self, rig, oven, monkeypatch):
        _between_check_and_commit(rig, monkeypatch, lambda: RigStopper(rig).stop(BEN, "race"))
        rig.write(oven.root, {"h1": 50.0}, actor=BEN)
        assert oven.writes[-1] == ("h1", 50.0), "a person's write passes the rig stop"
        said = [e for e in rig.recent if e.code == Code.WRITTEN_WHILE_STOPPED]
        assert len(said) == 1, "logged as written while stopped, as it is after a stop"

    def test_a_controllers_write_is_held(self, rig, oven, monkeypatch):
        controller = rig.attach_controller(oven.signals["h1"], oven.signals["zone1"])
        _between_check_and_commit(rig, monkeypatch, lambda: RigStopper(rig).stop(BEN, "race"))
        before = list(oven.writes)
        assert rig.write(oven.root, {"h1": 50.0}, by=controller) == {}
        assert ("h1", 50.0) not in oven.writes[len(before) :]


class TestLatchesUnderTheLock:
    def test_a_latch_is_set_and_reset_under_the_rigs_lock(self, rig, oven):
        held: list[tuple[bool, bool]] = []
        rig.stopping.latches.on_change.append(lambda _, on: held.append((on, rig.lock._is_owned())))
        RigStopper(rig).stop(BEN, "test")
        rig.stopping.reset("stop", BEN)
        assert held == [(True, True), (False, True)]

    def test_a_stuck_lock_does_not_hold_a_latch_up(self, rig, oven, monkeypatch):
        monkeypatch.setattr(stopping_mod, "LATCH_WAIT_S", 0.1)
        monkeypatch.setattr(stopping_mod, "DEVICE_STOP_S", 0.2)
        holding, release = threading.Event(), threading.Event()

        def stuck() -> None:
            with rig.lock:
                holding.set()
                release.wait(5.0)

        thread = threading.Thread(target=stuck, daemon=True)
        thread.start()
        assert holding.wait(2.0)
        try:
            report = RigStopper(rig).stop(BEN, "a delivery is stuck")
            assert rig.stopping.latches.rig_stop is not None, "latched all the same"
            assert report.devices[oven.name]["state"] == "failed"
        finally:
            release.set()
            thread.join(2.0)
        with pytest.raises(ConflictError, match="stopped"):
            rig.write(oven.root, {"h1": 50.0}, actor=AGENT)
