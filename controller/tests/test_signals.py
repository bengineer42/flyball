"""Signals, their outcomes, and the rig's named-signal registry."""

from __future__ import annotations

import threading

import pytest

from flyball.core.clock import Clock
from flyball.core.errors import ConflictError, NotFoundError
from flyball.core.signal import Outcome, Signal
from flyball.runtime.signals import Signals


def test_signal_settles_once_and_reports_how():
    s = Signal()
    assert not s.settled
    assert s.fire() and s.fired and s.outcome is Outcome.FIRED
    assert not s.interrupt(), "a settled signal stays settled"
    assert s.wait_outcome(0) is Outcome.FIRED


def test_signal_times_out():
    s = Signal(timeout=0.02)
    assert s.wait_outcome(1.0) is Outcome.TIMEOUT and s.timed_out


def test_on_settle_is_called_from_the_settling_thread():
    s = Signal()
    seen: list[str] = []
    s.on_settle = lambda sig: seen.append(threading.current_thread().name)
    t = threading.Thread(target=s.fire, name="settler")
    t.start()
    t.join()
    assert seen == ["settler"]


class TestSignals:
    def test_register_fire_and_remove(self):
        signals = Signals(Clock())
        s = Signal()
        state = signals.register("lid", s, "Close the lid")
        assert (
            state.outcome is Outcome.PENDING and signals.states()["lid"].message == "Close the lid"
        )
        assert signals.fire("lid") and s.fired
        assert signals.state("lid").outcome is Outcome.FIRED
        signals.remove("lid")
        with pytest.raises(NotFoundError):
            signals.state("lid")

    def test_outcome_is_pushed_to_the_cell_when_settled_from_anywhere(self):
        signals = Signals(Clock())
        s = Signal()
        signals.register("wait", s)
        version, changed = signals.latest.changed_since(0)
        assert changed["wait"].outcome is Outcome.PENDING
        s.interrupt()  # settled by the owner, not through the registry
        assert signals.latest.changed_since(version)[1]["wait"].outcome is Outcome.INTERRUPTED

    def test_one_name_at_a_time(self):
        signals = Signals(Clock())
        signals.register("x", Signal())
        with pytest.raises(ConflictError):
            signals.register("x", Signal())
        with pytest.raises(NotFoundError):
            signals.fire("nope")
