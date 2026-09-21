"""Polling devices on their period: the run state, going offline, restarting."""

from __future__ import annotations

import pytest

from flyball.foundation.device import Access, Committable, Device, Level, Reading, SignalSpec
from flyball.foundation.errors import NotFoundError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Watt
from flyball.runtime.polling import poll_period
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
    furnace.signals["sample"].override(poll_s=0.5)
    assert poll_period(furnace) == 0.5
    furnace.signals["setpoint"].override(poll_s=0.1)
    assert poll_period(furnace) == 0.5, "a non-publishing signal's period means nothing"
    heaters = Heaters(fresh("heaters"))
    heaters.signals["conditions"].restrict(Access.R)  # genuinely nothing publishing here
    heaters.poll_s = 1.0
    assert poll_period(heaters) is None


def test_start_polling_reads_on_the_period_and_delivers(rig, clock, furnace):
    furnace.signals["sample"].override(poll_s=0.5)
    rig.start_polling(furnace)
    run = rig.polling.run(furnace.name)
    assert run.period_s == 0.5 and run.running is True and run.last_read_ns is None
    with rig.polling.runs.watch():
        clock.advance(1.0)
    assert furnace.reads == 2
    zone1 = furnace.signals["zone1"]
    assert rig.latest[zone1] == Reading(zone1, clock.now_ns(), 20.0)
    run = rig.polling.runs.changed_since(0)[1][furnace.name]
    assert run.last_read_ns == clock.now_ns() and run.conditions == ()
    rig.stop()
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
    assert run.conditions[0].kind == "offline" and run.conditions[0].level is Level.ERROR
    assert "modbus timeout" in run.conditions[0].message
    event = rig.recent[-1]
    assert event.kind == "offline" and event.scope == "device" and event.subject == furnace.name
    clock.advance(5.0)
    assert furnace.reads == 2, "stays stopped until restarted"

    furnace.fail = False
    run = rig.polling.restart(furnace.name)
    assert run.conditions == () and run.running is True
    assert rig.recent[-1].kind == "restarted" and rig.recent[-1].subject == furnace.name
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
    assert run.conditions == () and run.last_read_ns == clock.now_ns(), "the device read fine"
    event = rig.recent[-1]
    assert event.kind == "delivery_failed" and event.scope == "rig"
    assert "a bug in a driver's commit" in event.message
    assert run.running is True


def test_a_slow_read_raises_a_warning_condition(rig, furnace):
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
    (condition,) = rig.polling.run(slow.name).conditions
    assert condition.kind == "slow" and condition.level is Level.WARNING
    assert rig.recent[-1].kind == "slow"
