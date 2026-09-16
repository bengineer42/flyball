"""The Bluesky adapter, checked against the shapes bluesky.protocols expects."""

from __future__ import annotations

import threading

from flyball.core.trigger import Trigger
from flyball.integrations.bluesky import DemandMovable, SourceReadable, Status
from helpers import sample


def test_source_readable_describes_channels_and_reads_the_latest_sample(
    rig, probe, temperature, clock
):
    readable = SourceReadable(rig, probe)
    key = f"{probe.name}.{temperature.name}"
    described = readable.describe()[key]
    assert described["units"] == "°C" and described["precision"] == 2
    assert (described["lower_ctrl_limit"], described["upper_ctrl_limit"]) == (-40.0, 125.0)
    assert readable.read() == {}
    rig.on_read([sample(probe, temperature, 21.5, clock.now_ns())])
    value = readable.read()[key]
    assert value["value"] == 21.5 and value["timestamp"] == clock.now_ns() / 1e9


def test_demand_movable_sets_and_reports(rig, heater):
    rig.add_actuator(heater)
    movable = DemandMovable(rig, heater)
    status = movable.set(42.0)
    status.wait(1.0)
    assert status.done and status.success and status.exception() is None
    assert heater.demands == [42.0] and heater.applied == 1
    assert movable.read()[f"{heater.name}.demand"]["value"] == 42.0


def test_status_callbacks_fire_once_settled_from_any_thread():
    signal = Trigger()
    status = Status(signal)
    seen: list[bool] = []
    status.add_callback(lambda s: seen.append(s.success))
    threading.Thread(target=signal.fire).start()
    status.wait(1.0)
    assert seen == [True]
    late: list[bool] = []
    status.add_callback(lambda s: late.append(s.done))
    assert late == [True], "a callback added after completion runs at once"


def test_status_reports_timeout_and_interrupt():
    timed = Status(Trigger(), timeout=0.01)
    assert isinstance(timed.exception(1.0), TimeoutError) and not timed.success
    s = Trigger()
    interrupted = Status(s)
    s.interrupt()
    assert isinstance(interrupted.exception(1.0), RuntimeError)
