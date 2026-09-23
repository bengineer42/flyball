"""Regression tests for the sim fault-injection review (N18, 23 Sep 2026).

N18: `ActivityOut.since_ns` (`TriggerState.since_ns`; then `WaitState`) was wall time, not rig
time: `Rig.__init__` built `Triggers(self.clock)` -- the `Clock` object
itself -- before a scaled sim clock was swapped onto `rig.clock` later
(`RunnerConfig.build`, `runtime/config.py`). `Triggers` now takes a clock
*provider*, read afresh on every `register`, so it follows a later
`rig.clock = ...` re-bind the way `Rig.router.now_ns` already did.
"""

from __future__ import annotations

from flyball_sim.clock import SteppedClock

from flyball.foundation.router import Trigger
from flyball.rig import Rig


def test_a_trigger_registered_after_the_clock_is_swapped_uses_the_new_clock():
    rig = Rig()
    clock = SteppedClock(1_000)
    rig.clock = clock  # what `RunnerConfig.build` does for a simulated rig, after `Rig()`
    clock.advance(5)

    state = rig.triggers.register("wait", Trigger())
    assert state.since_ns == clock.now_ns(), "stamped from the rig's current clock, not wall time"


def test_a_trigger_s_since_ns_advances_with_the_swapped_clock_not_real_time():
    rig = Rig()
    clock = SteppedClock(0)
    rig.clock = clock
    clock.advance(1_000)  # a sim clock may run far ahead of wall time

    state = rig.triggers.register("wait", Trigger())
    assert state.since_ns == 1_000 * 1_000_000_000
