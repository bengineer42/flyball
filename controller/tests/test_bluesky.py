"""The Bluesky adapter, checked against the shapes bluesky.protocols expects."""

from __future__ import annotations

import threading
from enum import Enum
from typing import Any

from flyball.core.device import Committable, Device
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, Role, Sample, SignalSpec
from flyball.core.trigger import Trigger
from flyball.core.units.si import Celsius
from flyball.integrations.bluesky import NodeReadable, SignalMovable, Status

TEMP = Quantity("temperature", Celsius)


class Probe(Device):
    TREE = (
        SignalSpec(
            name="temperature", quantity=TEMP, access=Access.RP, range=(-40.0, 125.0), precision=2
        ),
    )


class Heater(Committable):
    TREE = (SignalSpec(name="demand", quantity=TEMP, role=Role.DEMAND, access=Access.RPW),)


class Mode(Enum):
    ON = "on"
    OFF = "off"


class Multi(Device):
    """One signal of each dtype family, to check `describe`'s dtype mapping."""

    TREE = (
        SignalSpec(name="flag", quantity=TEMP, access=Access.RP, vtype=bool),
        SignalSpec(name="mode", quantity=TEMP, access=Access.RP, vtype=Mode),
        SignalSpec(name="items", quantity=TEMP, access=Access.RP, vtype=tuple[int, ...]),
        SignalSpec(name="blob", quantity=TEMP, access=Access.RP, vtype=dict[str, Any]),
    )


def test_node_readable_describes_signals_and_reads_the_latest_sample(rig, fresh, clock):
    probe = Probe(fresh("probe"))
    rig.add_device(probe)
    readable = NodeReadable(rig, probe.root)
    key = probe.signals["temperature"].address
    described = readable.describe()[key]
    assert described["units"] == "°C" and described["precision"] == 2
    assert (described["lower_ctrl_limit"], described["upper_ctrl_limit"]) == (-40.0, 125.0)
    assert described["dtype"] == "number"
    assert readable.read() == {}
    rig.on_samples([Sample(probe.root, clock.now_ns(), {probe.signals["temperature"]: 21.5})])
    value = readable.read()[key]
    assert value["value"] == 21.5 and value["timestamp"] == clock.now_ns() / 1e9


def test_describe_dtype_follows_the_signal_s_declared_type(rig, fresh):
    multi = Multi(fresh("multi"))
    rig.add_device(multi)
    described = NodeReadable(rig, multi.root).describe()
    assert described[multi.signals["flag"].address]["dtype"] == "boolean"
    assert described[multi.signals["mode"].address]["dtype"] == "string"
    assert described[multi.signals["items"].address]["dtype"] == "array"
    assert described[multi.signals["blob"].address]["dtype"] == "object"


def test_signal_movable_sets_and_reports(rig, fresh):
    heater = Heater(fresh("heater"))
    rig.add_device(heater)
    demand = heater.signals["demand"]
    movable = SignalMovable(rig, demand)
    assert movable.describe()[movable.name]["dtype"] == "number"
    status = movable.set(42.0)
    status.wait(1.0)
    assert status.done and status.success and status.exception() is None
    assert heater.written[demand].value == 42.0
    assert movable.read()[demand.address]["value"] == 42.0


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
