"""Polling devices on their period: the run state, going offline, restarting."""

from __future__ import annotations

import logging
import threading
import time

import pytest

from flyball.foundation.device import Access, Committable, Device, Reading, Severity, SignalSpec
from flyball.foundation.errors import NotFoundError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Watt
from flyball.rig import Rig, poll_period, polling
from test_rig_devices import Furnace


class Heaters(Device):
    """Write-only: nothing publishes, so nothing to poll."""

    TREE = (SignalSpec(name="heater1", quantity=Quantity("power", Watt), access=Access.W),)


@pytest.fixture
def furnace(rig, fresh) -> Furnace:
    furnace = Furnace(fresh("furnace"))
    furnace.poll_s = 1.0
    rig.add_device(furnace)
    return furnace


def test_the_period_is_the_smallest_in_the_tree(fresh):
    furnace = Furnace(fresh("furnace"))
    assert poll_period(furnace) is None
    furnace.poll_s = 1.0
    assert poll_period(furnace) == 1.0
    furnace.signals["sample"].set_meta(poll_s=0.5)
    assert poll_period(furnace) == 0.5
    furnace.signals["setpoint"].set_meta(poll_s=0.1)
    assert poll_period(furnace) == 0.5, "a non-publishing signal's period means nothing"
    heaters = Heaters(fresh("heaters"))  # genuinely nothing publishing here
    heaters.poll_s = 1.0
    assert poll_period(heaters) is None


def test_start_polling_reads_on_the_period_and_delivers(rig, clock, furnace):
    furnace.signals["sample"].set_meta(poll_s=0.5)
    rig.start_polling(furnace)
    run = rig.polling.run(furnace.name)
    assert run.period_s == 0.5 and run.running is True and run.last_read_ns is None
    with rig.polling.runs.watch():
        clock.advance(1.0)
    assert furnace.reads == 2
    zone1 = furnace.signals["zone1"]
    assert rig.latest[zone1] == Reading(zone1, clock.now_ns(), 20.0)
    run = rig.polling.runs.changed_since(0)[1][furnace.name]
    assert run.last_read_ns == clock.now_ns() and rig.conditions.of(furnace) == []
    rig.close()
    assert rig.polling.run(furnace.name).running is False
    clock.advance(1.0)
    assert furnace.reads == 2


def test_a_device_with_nothing_to_poll_is_left_alone(rig, fresh):
    heaters = Heaters(fresh("heaters"))
    heaters.poll_s = 1.0
    rig.add_device(heaters)
    rig.start_polling(heaters)
    assert heaters.name not in rig.polling.periodic and heaters.name not in rig.polling.by_name


def test_a_read_that_yields_nothing_keeps_the_last_read(rig, clock, furnace):
    rig.start_polling(furnace)
    clock.advance(1.0)
    first = clock.now_ns()
    furnace.due = False
    clock.advance(1.0)
    assert furnace.reads == 2
    assert rig.polling.run(furnace.name).last_read_ns == first


def test_offline_on_an_exception_with_a_condition_and_an_event(rig, clock, furnace):
    rig.start_polling(furnace)
    clock.advance(1.0)
    furnace.fail = True
    clock.advance(1.0)
    run = rig.polling.run(furnace.name)
    assert run.running is False
    (offline,) = rig.conditions.of(furnace)
    assert offline.code == "offline" and offline.severity is Severity.ERROR
    assert "modbus timeout" in offline.message
    event = rig.recent[-1]
    assert event.code == "offline" and event.scope == "device" and event.subject == furnace.name
    assert event.edge == "raised"
    clock.advance(5.0)
    assert furnace.reads == 2, "stays stopped until restarted"

    furnace.fail = False
    run = rig.polling.restart(furnace.name)
    assert rig.conditions.of(furnace) == [] and run.running is True
    event = rig.recent[-1]
    assert (event.code, event.edge, event.subject) == ("offline", "cleared", furnace.name)
    clock.advance(1.0)
    assert furnace.reads == 3 and rig.polling.run(furnace.name).last_read_ns == clock.now_ns()
    with pytest.raises(NotFoundError, match="Polled device 'nope' not found"):
        rig.polling.restart("nope")


def test_a_failure_downstream_of_a_read_is_the_rig_s(rig, clock, furnace, fresh):
    class Broken(Committable):
        def commit(self, time_ns: int) -> None:
            raise ValueError("a bug in a driver's commit")

    rig.add_device(broken := Broken(fresh("broken")))
    rig.bind_inputs(broken, {"in": f"{furnace.name}.zone1"})
    rig.start_polling(furnace)
    clock.advance(1.0)
    run = rig.polling.run(furnace.name)
    assert rig.conditions.of(furnace) == [], "the device read fine"
    assert run.last_read_ns == clock.now_ns()
    event = rig.recent[-1]
    assert event.code == "commit_failed" and event.scope == "device"
    assert event.subject == broken.name, "the committing device's, not the polled one's"
    assert "a bug in a driver's commit" in event.message
    assert run.running is True


def test_a_failure_in_the_delivery_itself_is_delivery_failed(rig, clock, furnace, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("a bug downstream")

    monkeypatch.setattr(rig, "_commit", broken)
    rig.start_polling(furnace)
    clock.advance(1.0)
    run = rig.polling.run(furnace.name)
    assert rig.conditions.of(furnace) == [] and run.running is True
    event = rig.recent[-1]
    assert event.code == "delivery_failed" and event.scope == "rig"
    assert "a bug downstream" in event.message


def test_three_slow_reads_raise_a_warning_condition(rig, furnace):
    class Slow(Furnace):
        def read(self, time_ns, node=None):
            rig.clock.sleep(2.0)  # the rig's time is what "slow" is judged in
            return super().read(time_ns, node)

    slow = Slow(furnace.name + "_slow")
    slow.poll_s = 1.0
    rig.add_device(slow)
    rig.start_polling(slow)
    rig.polling.stop_all()
    rig.polling._read(slow)
    rig.polling._read(slow)
    assert rig.conditions.of(slow) == [], "two slow reads are not yet slow"
    rig.polling._read(slow)
    (condition,) = rig.conditions.of(slow)
    assert condition.code == "slow" and condition.severity is Severity.WARNING
    assert rig.recent[-1].code == "slow"


def test_stop_gives_up_on_a_read_stuck_in_its_driver(monkeypatch, caplog, fresh):
    """A hung read must not hang `rig.close()` -- SIGTERM and a daemon Restart run it."""
    monkeypatch.setattr(polling, "STOP_JOIN_S", 0.3)
    entered, release = threading.Event(), threading.Event()

    class Stuck(Furnace):
        def read(self, time_ns, node=None):
            entered.set()
            release.wait()
            return super().read(time_ns, node)

    rig = Rig()  # wall time: the poller runs on its own thread
    stuck = Stuck(fresh("stuck"))
    stuck.poll_s = 0.01
    rig.add_device(stuck)
    rig.start_polling(stuck)
    try:
        assert entered.wait(2.0), "the read never started"
        started = time.monotonic()
        with caplog.at_level(logging.WARNING, logger="flyball.polling"):
            rig.close()
        took = time.monotonic() - started
        assert took < 0.3 + 0.5, f"stop took {took:.2f} s"
        stuck_logs = [r for r in caplog.records if stuck.name in r.getMessage()]
        assert len(stuck_logs) == 1, [r.getMessage() for r in caplog.records]
        assert rig.polling.run(stuck.name).running is False
    finally:
        release.set()


def test_revive_restarts_only_an_offline_polled_device(rig, clock, furnace):
    rig.start_polling(furnace)
    clock.advance(1.0)
    assert rig.polling.revive(furnace.name) is False, "running: nothing to do"
    furnace.fail = True
    clock.advance(1.0)
    assert rig.polling.run(furnace.name).running is False
    furnace.fail = False
    assert rig.polling.revive(furnace.name) is True
    assert rig.polling.run(furnace.name).running is True
    assert rig.polling.revive("nope") is False, "not polled: not an error"


def test_a_restart_of_a_still_broken_device_ends_offline_again(rig, clock, furnace):
    rig.start_polling(furnace)
    clock.advance(1.0)
    furnace.fail = True
    clock.advance(1.0)
    rig.polling.restart(furnace.name)  # still broken
    clock.advance(1.0)
    run = rig.polling.run(furnace.name)
    assert run.running is False and [c.code for c in rig.conditions.of(furnace)] == ["offline"]
    edges = [(e.code, e.edge) for e in rig.recent if e.subject == furnace.name]
    assert edges[-2:] == [("offline", "cleared"), ("offline", "raised")], (
        "the offline is raised again after the restart cleared it"
    )
