"""The recorder keeps going: a NaN law output, a device removed and added back."""

from __future__ import annotations

import math

from flyball_sim.clock import SteppedClock

from flyball.control import PI
from flyball.foundation.device import Access, Device, Role, Sample, SignalSpec
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Watt
from flyball.model.controller import Controller
from flyball.model.feedforward import NoFeedforward
from flyball.record.sqlite import SqliteStore
from flyball.record.types import Tick

START_NS = 1_700_000_000_000_000_000


class Probe(Device):
    TREE = (
        SignalSpec(name="temperature", quantity=Quantity("temperature", Celsius), access=Access.RP),
    )


class Heater(Device):
    TREE = (
        SignalSpec(
            name="power",
            quantity=Quantity("power", Watt),
            access=Access.RPW,
            role=Role.DEMAND,
            limits=(0.0, 10.0),
        ),
    )


def _session(fresh):
    probe, heater = Probe(fresh("probe")), Heater(fresh("heater"))
    controller = Controller(
        SteppedClock(0),
        heater.signals["power"],
        probe.signals["temperature"],
        law=PI(kp=1.0),
        feedforward=NoFeedforward(),
    )
    store = SqliteStore(":memory:")
    writer = store.open_session(start_ns=START_NS, config={"name": "t"})
    for device in (probe, heater):
        writer.declare_device(device)
    for signal in (probe.signals["temperature"], heater.signals["power"]):
        writer.declare_signal(signal)
    writer.declare_controller(controller)
    return store, writer, controller, probe, heater


def test_a_tick_whose_correction_is_nan_is_kept_not_fatal(fresh):
    store, writer, controller, _, _ = _session(fresh)
    writer.write_tick(Tick(controller.name, 1_000_000_000, "regulating", 5.0, output_value=5.0))
    writer.write_tick(
        Tick(controller.name, 2_000_000_000, "regulating", math.nan, output_value=math.nan)
    )
    writer.write_tick(Tick(controller.name, 3_000_000_000, "regulating", 6.0, output_value=6.0))

    ticks = store.ticks(writer.session.id, controller.name)
    assert [t.correction for t in ticks] == [5.0, None, 6.0], (
        "NULL stands for a NaN; the rest carry on"
    )


def test_a_device_removed_and_added_back_keeps_its_row(fresh):
    store, writer, _, probe, _ = _session(fresh)
    writer.write_samples([
        Sample(probe.root, START_NS + 1_000_000_000, {probe.signals["temperature"]: 20.0})
    ])

    again = Probe(probe.name)  # a version restore builds a new object at the same address
    writer.declare_device(again)
    writer.declare_signal(again.signals["temperature"])
    writer.write_samples([
        Sample(again.root, START_NS + 2_000_000_000, {again.signals["temperature"]: 21.0})
    ])

    series = store.series(writer.session.id, probe.signals["temperature"].address)
    values = [p.value for p in series.points]
    assert values == [20.0, 21.0], "one signal, both readings, no UNIQUE failure"


def test_a_writable_signal_added_back_can_still_carry_a_controller(fresh):
    _, writer, controller, probe, heater = _session(fresh)
    again = Heater(heater.name)
    writer.declare_device(again)
    writer.declare_signal(again.signals["power"])
    rebuilt = Controller(
        SteppedClock(0),
        again.signals["power"],
        probe.signals["temperature"],
        law=PI(kp=1.0),
        feedforward=NoFeedforward(),
    )
    writer.declare_controller(rebuilt)  # already declared by name: accepted, no error
    assert rebuilt.name == controller.name
