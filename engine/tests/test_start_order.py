"""Pins for the runner's start-up order and what survives a restart (core refactor Part A).

`tests/test_wf3_stop.py` covers a latch re-applying its stop when the rig attaches a store;
these check the same through the runner's own start, and pin the order the refactor fixes:
build with nothing running, restore what the last run kept, then start (D-094).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flyball.foundation.actor import Actor
from flyball.rig import Rig
from flyball.rig.stopping import RigStopper
from flyball.runner.starting import start_with_store
from flyball.runtime.config import load_rig_config

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"
BEN = Actor("ben", "human", "http", sid="s1")


@pytest.fixture
def oven():
    return load_rig_config(EXAMPLES / "oven.yaml")


def test_a_rig_stop_survives_a_restart_through_the_runner(tmp_path, oven):
    path = tmp_path / "s.sqlite"
    rig, store = start_with_store(oven, store_path=path)
    try:
        RigStopper(rig).stop(BEN, "test")
        assert rig.stopping.latches.rig_stop is not None
    finally:
        rig.close()
        store.close()

    rig, store = start_with_store(oven, store_path=path)
    try:
        assert rig.stopping.latches.rig_stop is not None, "a restart un-stopped the rig"
    finally:
        rig.close()
        store.close()


@pytest.mark.xfail(
    strict=True,
    reason="D-094: polling starts before the saved state is restored; fixed in Part A step 4",
)
def test_nothing_polls_before_the_saved_state_is_restored(tmp_path, oven, monkeypatch):
    restored_at_first_poll: list[bool] = []
    start_polling = Rig.start_polling

    def spy(self: Rig, device):
        if not restored_at_first_poll:
            restored_at_first_poll.append(self.values.store is not None)
        return start_polling(self, device)

    monkeypatch.setattr(Rig, "start_polling", spy)
    rig, store = start_with_store(oven, store_path=tmp_path / "s.sqlite")
    try:
        assert restored_at_first_poll == [True], "a device was polled before the restore"
    finally:
        rig.close()
        store.close()
