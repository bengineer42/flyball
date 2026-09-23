"""Triggers, their outcomes, and the rig's named-trigger registry."""

from __future__ import annotations

import threading

import pytest

from flyball.foundation.errors import ConflictError, NotFoundError
from flyball.foundation.router import Outcome, Trigger
from flyball.foundation.time import Clock
from flyball.rig import Triggers


def test_signal_settles_once_and_reports_how():
    s = Trigger()
    assert not s.settled
    assert s.fire() and s.fired and s.outcome is Outcome.FIRED
    assert not s.interrupt(), "a settled signal stays settled"
    assert s.wait_outcome(0) is Outcome.FIRED


def test_signal_times_out():
    s = Trigger(timeout=0.02)
    assert s.wait_outcome(1.0) is Outcome.TIMEOUT and s.timed_out


def test_on_settle_is_called_from_the_settling_thread():
    s = Trigger()
    seen: list[str] = []
    s.on_settle = lambda sig: seen.append(threading.current_thread().name)
    t = threading.Thread(target=s.fire, name="settler")
    t.start()
    t.join()
    assert seen == ["settler"]


class TestSignals:
    def test_register_fire_and_remove(self):
        signals = Triggers(Clock())
        s = Trigger()
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
        signals = Triggers(Clock())
        s = Trigger()
        signals.register("wait", s)
        version, changed = signals.latest.changed_since(0)
        assert changed["wait"].outcome is Outcome.PENDING
        s.interrupt()  # settled by the owner, not through the registry
        assert signals.latest.changed_since(version)[1]["wait"].outcome is Outcome.INTERRUPTED

    def test_one_name_at_a_time(self):
        signals = Triggers(Clock())
        signals.register("x", Trigger())
        with pytest.raises(ConflictError):
            signals.register("x", Trigger())
        with pytest.raises(NotFoundError):
            signals.fire("nope")

    def test_a_signal_settling_before_its_hook_is_set_still_shows_settled(self):
        """Settled between `register` reading the outcome and setting `on_settle`."""
        s = Trigger()

        class Firing(Clock):
            def now_ns(self) -> int:  # register stamps the state after reading the outcome
                s.fire()
                return super().now_ns()

        signals = Triggers(Firing())
        signals.register("early", s)
        assert signals.state("early").outcome is Outcome.FIRED
        assert signals.latest.get("early").outcome is Outcome.FIRED

    def test_a_signal_settling_as_its_hook_is_set_is_not_overwritten_as_pending(self):
        """Settled between `on_settle` being set and the pending state being published."""

        class FiresWhenHooked(Trigger):
            def __init__(self) -> None:
                self._hook = None
                super().__init__()

            @property
            def on_settle(self):  # type: ignore[override]
                return self._hook

            @on_settle.setter
            def on_settle(self, hook) -> None:
                self._hook = hook
                if hook is not None:
                    self.fire()

        signals = Triggers(Clock())
        signals.register("hooked", FiresWhenHooked())
        assert signals.state("hooked").outcome is Outcome.FIRED
        assert signals.latest.get("hooked").outcome is Outcome.FIRED
