"""Wave 3 stage A: the software stop, its latch, `on_fault` v1 and the permissive.

Each device here is small and counts what reaches its hardware (`writes`), so a test can
say what a stop wrote, and that a latch refused everything else.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flyball_sim import SteppedClock

from conftest import TestClient
from flyball.control.laws import P
from flyball.control.setpoint import LinearRampSetpoint
from flyball.foundation.device import (
    KEEP,
    Access,
    Code,
    Committable,
    Demand,
    DeviceEntry,
    Permissive,
    Readout,
    Role,
    Sample,
    SignalSpec,
    command,
    invalid,
)
from flyball.foundation.device.entry import _set_stops
from flyball.foundation.errors import ConflictError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Watt
from flyball.foundation.time import Duration
from flyball.interfaces.server import create_app, set_programmer, set_rig
from flyball.interfaces.server import principal as principal_mod
from flyball.interfaces.server.deps import get_dialect
from flyball.interfaces.server.dialect import program_from_document
from flyball.model.controller import ControllerMode, FaultAction, OnFault
from flyball.record.sqlite import SqliteStore
from flyball.rig import Rig
from flyball.rig import stopping as stopping_mod
from flyball.rig.stopping import Actor, RigStopper, resolve_output, stop_plan
from flyball.runtime.config import AuthConfig
from flyball.sequencing import Program, Programmer

TEMP = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)

BEN = Actor("ben", "s1", "human", "http")
AGENT = Actor("claude", "s2", "agent", "mcp")


class Oven(Committable):
    """Three heaters on one device: `h1`, `h2` declare `off=0`, `h3` none; zone readouts."""

    TREE = (
        SignalSpec(name="zone1", quantity=TEMP, access=Access.RP),
        SignalSpec(name="zone2", quantity=TEMP, access=Access.RP),
        SignalSpec(
            name="h1",
            quantity=POWER,
            role=Role.DEMAND,
            access=Access.RPW,
            limits=(0.0, 100.0),
            off=0.0,
        ),
        SignalSpec(
            name="h2",
            quantity=POWER,
            role=Role.DEMAND,
            access=Access.RPW,
            limits=(0.0, 100.0),
            off=0.0,
        ),
        SignalSpec(
            name="h3", quantity=POWER, role=Role.DEMAND, access=Access.RPW, limits=(0.0, 100.0)
        ),
    )

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.writes: list[tuple[str, float]] = []
        self.fail = False

    def commit(self, time_ns: int) -> None:
        if self.fail:
            raise OSError("bus down")
        for signal, value in self.staged.items():
            self.writes.append((signal.name, value))


class Pumps(Committable):
    """A composite with a stop command: `flow` is a readback, moved only by commands."""

    flow = Demand("flow", "Flow", POWER, access=Access.RP, limits=(0.0, 10.0), off=0.0)
    running = Readout("running", "Running", initial=0.0)

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.stops = 0
        self.broken = False

    @command(writes=("flow",))
    def run(self, value: float) -> None:
        """Run the pumps at `value`."""
        self.running.push(1.0)
        self.flow.push(value)

    @command(stops=True, interrupts=True)
    def stop(self) -> None:
        """Both pumps off."""
        if self.broken:
            raise OSError("pump bus down")
        self.stops += 1
        self.running.push(0.0)
        self.flow.push(0.0)


class Doser(Committable):
    """A long `dose` that waits, as `dosing_pump.dispense` does."""

    rate = Demand("rate", "Rate", POWER, limits=(0.0, 1.0), off=0.0)

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.started = threading.Event()
        self.ended_early: bool | None = None

    @command(long=True, writes=("rate",))
    def dose(self, seconds: float) -> None:
        """Dose for `seconds`."""
        self.started.set()
        self.ended_early = self.wait(seconds)


@pytest.fixture
def oven(rig: Rig, fresh: Any) -> Oven:
    device = Oven(fresh("oven"))
    rig.add_device(device)
    now = rig.clock.now_ns()
    rig.on_samples([Sample(device.root, now, {device.signals["zone1"]: 20.0})])
    rig.on_samples([Sample(device.root, now, {device.signals["zone2"]: 20.0})])
    return device


def _set(rig: Rig, device: Committable, **values: float) -> None:
    rig.write(device.root, values, actor=BEN)


def _stop(rig: Rig, program: Any = None) -> stopping_mod.StopReport:
    return RigStopper(rig, program).stop(BEN, "test")


def _prompt() -> Program:
    """A program that waits for an answer that never comes (a stepped clock never ends it)."""
    return program_from_document({"steps": [{"prompt": "hold on"}]}, get_dialect())


def _events(rig: Rig, code: str) -> list[Any]:
    return [e for e in rig.recent if e.code == code]


# region Resolution


class TestResolution:
    def test_you_said_then_off_then_keep(self, oven: Oven):
        h1, h2, h3 = (oven.signals[n] for n in ("h1", "h2", "h3"))
        assert resolve_output(h1).value == 0.0 and resolve_output(h1).origin == "off"
        _set_stops(oven, {"h1": KEEP, "h2": 5.0})
        assert (resolve_output(h1).value, resolve_output(h1).origin) == (None, "you_said")
        assert (resolve_output(h2).value, resolve_output(h2).origin) == (5.0, "you_said")
        assert (resolve_output(h3).value, resolve_output(h3).origin) == (None, "nobody_said")

    def test_a_stop_value_is_checked_at_load(self, oven: Oven):
        with pytest.raises(ValueError, match="outside"):
            _set_stops(oven, {"h1": 150.0})
        with pytest.raises(ValueError, match="not a writable demand"):
            _set_stops(oven, {"zone1": 0.0})
        with pytest.raises(ValueError, match="not a signal"):
            _set_stops(oven, {"nope": 0.0})

    def test_values_are_refused_on_a_command_stop_device(self, rig: Rig, fresh: Any):
        pumps = Pumps(fresh("pumps"))
        assert type(pumps).stop_command == "stop"
        with pytest.raises(ValueError, match="stopped by its driver's 'stop' command"):
            _set_stops(pumps, {"flow": 0.0})

    def test_one_stop_per_driver_and_never_long(self):
        with pytest.raises(TypeError, match="both marked stops=True"):

            class Two(Committable):
                @command(stops=True)
                def a(self) -> None:
                    """A."""

                @command(stops=True)
                def b(self) -> None:
                    """B."""

        with pytest.raises(TypeError, match="cannot be long"):
            command(stops=True, long=True)(lambda self: None)

    def test_the_plan_sorts_and_warns(self, rig: Rig, oven: Oven):
        rig.attach_controller(oven.signals["h3"], oven.signals["zone1"], law=P(kp=1.0))
        rig.attach_controller(oven.signals["h1"], oven.signals["zone2"], law=P(kp=1.0))
        rows = {r["address"]: r for r in stop_plan(rig)}
        assert [r["origin"] for r in stop_plan(rig)] == ["off", "off", "nobody_said"]
        assert rows[f"{oven.name}.h3"]["stop"] == KEEP
        assert "stays where it was" in rows[f"{oven.name}.h3"]["warnings"][0]
        assert "freeze holds this output" in rows[f"{oven.name}.h1"]["warnings"][0]
        assert all(r["covered_if_flyball_dies"] is False for r in rows.values())

    def test_the_span_helper_declares_off_only_when_asked_and_never_across_0(self):
        from flyball.hardware.spanned_demand import spanned_signal_spec

        bare = Quantity("drive", Watt)
        assert spanned_signal_spec("d", None, None, None, bare=bare).off is None
        assert spanned_signal_spec("d", None, None, None, bare=bare, off_at_zero=True).off == 0.0
        assert (
            spanned_signal_spec("d", "°C", None, (10.0, 40.0), bare=bare, off_at_zero=True).off
            == 10.0
        )
        assert (
            spanned_signal_spec("d", "W", None, (-5.0, 5.0), bare=bare, off_at_zero=True).off
            is None
        )


# endregion

# region The stop


class TestTheStop:
    def test_writes_off_keeps_the_rest_and_latches(self, rig: Rig, oven: Oven):
        oven.signals["h1"].narrow((10.0, 90.0))  # off is written outside the limits
        _set(rig, oven, h1=50.0, h2=60.0, h3=70.0)
        _set_stops(oven, {"h2": KEEP})
        controller = rig.attach_controller(oven.signals["h1"], oven.signals["zone1"], law=P(kp=1))
        controller.regulate(25.0)
        oven.writes.clear()

        report = _stop(rig)

        assert oven.writes == [("h1", 0.0)], "off past the limits; h2 kept (you said); h3 none"
        stop = report.devices[oven.name]
        assert stop["state"] == "stopped"
        assert stop["written"] == {f"{oven.name}.h1": 0.0}
        assert stop["kept"] == {f"{oven.name}.h2": 60.0, f"{oven.name}.h3": 70.0}
        assert report.latched and not report.interim
        assert controller.mode is ControllerMode.MANUAL
        assert rig.conditions.get(rig, Code.STOPPED) is not None
        (applied,) = _events(rig, Code.STOP_APPLIED)
        assert applied.details["kept"] == stop["kept"]
        assert "safe" not in repr(report).lower()

    def test_automatic_writers_are_refused_and_a_person_goes_through(self, rig: Rig, oven: Oven):
        controller = rig.attach_controller(oven.signals["h1"], oven.signals["zone1"], law=P(kp=1))
        _stop(rig)
        oven.writes.clear()
        with pytest.raises(ConflictError, match="stopped by ben"):
            rig.write(oven.root, {"h2": 5.0}, writer="program")
        with pytest.raises(ConflictError, match="reset it to write"):
            rig.write(oven.root, {"h2": 5.0}, actor=AGENT)
        assert rig.write(oven.root, {"h1": 5.0}, by=controller) == {}, "held, not raised"
        with pytest.raises(ConflictError, match="reset it first"):
            controller.regulate(30.0)
        assert oven.writes == []

        rig.write(oven.root, {"h2": 5.0}, actor=BEN)  # a person, forced and logged
        assert oven.writes == [("h2", 5.0)]
        (logged,) = _events(rig, Code.WRITTEN_WHILE_STOPPED)
        assert logged.details == {"value": 5.0, "by": "ben"}
        assert rig.stopping.latches.rig_stop is not None, "the latch stays"

    def test_reset_resumes_nothing(self, rig: Rig, oven: Oven):
        controller = rig.attach_controller(oven.signals["h1"], oven.signals["zone1"], law=P(kp=1))
        controller.regulate(25.0)
        _stop(rig)
        rig.stopping.reset("stop", BEN)
        assert controller.mode is ControllerMode.MANUAL
        assert rig.conditions.get(rig, Code.STOPPED) is None
        (reset,) = _events(rig, Code.RESET)
        assert reset.details["cause"] == "stop"
        controller.regulate(25.0)  # allowed again
        rig.write(oven.root, {"h2": 1.0}, writer="program")

    def test_a_stop_replaces_what_was_staged(self, rig: Rig, oven: Oven):
        oven.fail = True
        with pytest.raises(OSError):
            _set(rig, oven, h3=40.0)
        assert dict.get(oven.staged, oven.signals["h3"]) == 40.0, "kept for a retry (A6)"
        assert oven in rig._retries
        oven.fail = False
        _stop(rig)
        assert oven.signals["h3"] not in dict.keys(oven.staged)
        assert oven not in rig._retries, "the retry is cancelled"
        assert ("h3", 40.0) not in oven.writes

    def test_a_latched_device_commits_nothing_a_delivery_brings(self, rig: Rig, fresh: Any):
        oven = Oven(fresh("oven"))
        rig.add_device(oven)
        _stop(rig)
        oven.writes.clear()
        dict.__setitem__(oven.staged, oven.signals["h3"], 9.0)  # what an input landing stages
        rig._committing((oven,), rig.clock.now_ns())
        assert oven.writes == [] and not dict.__len__(oven.staged)

    def test_a_values_device_is_left_alone(self, rig: Rig, fresh: Any):
        from flyball.foundation.device.values import ValuesConfig

        values = ValuesConfig.model_validate({"values": {"dry": {"initial": 1.0}}}).build(
            fresh("vals")
        )
        rig.add_device(values)
        report = _stop(rig)
        assert values.name not in report.devices
        rig.write(values.root, {"dry": 2.0}, writer="program")  # a stop leaves it writable

    def test_a_stop_command_device(self, rig: Rig, fresh: Any):
        pumps = Pumps(fresh("pumps"))
        rig.add_device(pumps)
        rig.run_command(pumps, "run", {"value": 4.0})
        report = _stop(rig)
        assert pumps.stops == 1
        assert report.devices[pumps.name]["state"] == "stopped"
        assert report.devices[pumps.name]["written"] == {f"{pumps.name}.flow": 0.0}
        with pytest.raises(ConflictError, match="stopped by ben"):
            rig.run_command(pumps, "run", {"value": 4.0})  # automatic: refused
        rig.run_command(pumps, "run", {"value": 3.0}, actor=BEN)  # a person: through
        rig.run_command(pumps, "stop")  # the stop command itself is never refused
        assert pumps.stops == 2

    def test_a_stop_command_that_fails_falls_back_to_off(self, rig: Rig, fresh: Any):
        pumps = Pumps(fresh("pumps"))
        rig.add_device(pumps)
        pumps.broken = True
        report = _stop(rig)
        stop = report.devices[pumps.name]
        assert stop["state"] == "failed"
        assert "pump bus down" in stop["message"] and "fallback" in stop["message"]

    def test_a_stuck_lock_does_not_hold_the_stop(self, rig: Rig, oven: Oven, monkeypatch):
        monkeypatch.setattr(stopping_mod, "DEVICE_STOP_S", 0.3)
        monkeypatch.setattr("flyball.rig.rig.REPLACE_WAIT_S", 0.05)
        held, release = threading.Event(), threading.Event()

        def stuck() -> None:
            with rig.lock:
                held.set()
                release.wait(5)

        thread = threading.Thread(target=stuck, daemon=True)
        thread.start()
        assert held.wait(2)
        began = time.monotonic()
        try:
            report = _stop(rig)
        finally:
            release.set()
            thread.join(2)
        assert time.monotonic() - began < 2.0
        assert report.devices[oven.name]["state"] == "failed"
        assert rig.stopping.latches.rig_stop is not None, "latched all the same"

    def test_a_stop_cancels_a_long_command(self, fresh: Any):
        rig = Rig()  # wall time: a stepped clock steps past a wait at once
        doser = Doser(fresh("doser"))
        rig.add_device(doser)
        done = threading.Event()
        threading.Thread(
            target=lambda: (rig.run_command(doser, "dose", {"seconds": 60.0}), done.set()),
            daemon=True,
        ).start()
        assert doser.started.wait(2)
        report = _stop(rig)
        assert done.wait(2) and doser.ended_early is True
        assert report.devices[doser.name]["state"] == "stopped"

    def test_the_program_is_interrupted(self, rig: Rig, oven: Oven):
        programmer = Programmer(rig)
        programmer.start(_prompt())
        report = RigStopper(rig, programmer).stop(BEN, "")
        assert report.program_interrupted is True and not programmer.running


# endregion

# region Shutdown and restart


class TestShutdownAndRestart:
    def test_shutdown_stops_unless_kept_and_latches_nothing(self, rig: Rig, fresh: Any):
        a, b = Oven(fresh("a")), Oven(fresh("b"))
        for device in (a, b):
            rig.add_device(device)
            _set(rig, device, h1=50.0)
            device.writes.clear()
        rig.entries[b.name] = DeviceEntry(driver="oven", on_shutdown="keep")
        report = RigStopper(rig).shutdown(BEN)
        assert a.writes == [("h1", 0.0), ("h2", 0.0)] and b.writes == []
        assert set(report.devices) == {a.name}
        assert rig.stopping.latches.rig_stop is None

    def test_on_shutdown_keep_writes_nothing(self, rig: Rig, oven: Oven):
        _set(rig, oven, h1=50.0)
        oven.writes.clear()
        RigStopper(rig).shutdown(BEN, keep=True)
        assert oven.writes == []

    def test_a_persisted_latch_stops_again_at_start(self, tmp_path, fresh: Any):
        store = SqliteStore(tmp_path / "s.sqlite")
        first = Rig()
        first.clock = SteppedClock(0)
        name = fresh("oven")
        first.add_device(Oven(name))
        first.stopping.latches.attach(store)
        _stop(first)
        assert [row.cause for row in store.latches()] == ["stop"]

        second = Rig()
        second.clock = SteppedClock(0)
        oven = Oven(name)
        second.add_device(oven)
        _set(second, oven, h1=50.0)  # the build's value, say
        oven.writes.clear()
        restored = second.stopping.attach(store)
        assert [latch.cause for latch in restored] == ["stop"]
        assert oven.writes == [("h1", 0.0), ("h2", 0.0)], "the stop is applied again"
        with pytest.raises(ConflictError, match="stopped by ben"):
            second.write(oven.root, {"h2": 1.0}, writer="program")
        second.stopping.reset("stop", BEN)
        assert store.latches() == []
        store.close()


# endregion

# region on_fault


def _fault(rig: Rig, device: Oven, signal: str, seconds: float, clock: SteppedClock) -> None:
    """`signal` invalid for `seconds`, one reading a second."""
    for _ in range(int(seconds)):
        clock.advance(1.0)
        rig.on_samples([
            Sample(device.root, clock.now_ns(), {device.signals[signal]: invalid("x")})
        ])


def _good(rig: Rig, device: Oven, signal: str, value: float, clock: SteppedClock) -> None:
    clock.advance(1.0)
    rig.on_samples([Sample(device.root, clock.now_ns(), {device.signals[signal]: value})])


class TestOnFault:
    def _loop(self, rig: Rig, oven: Oven, on_fault: OnFault, out: str = "h1", src: str = "zone1"):
        controller = rig.attach_controller(
            oven.signals[out], oven.signals[src], law=P(kp=1.0), on_fault=on_fault
        )
        _good(rig, oven, src, 20.0, rig.clock)  # type: ignore[arg-type]
        controller.regulate(25.0)
        return controller

    def test_freeze_never_acts(self, rig: Rig, oven: Oven, clock: SteppedClock):
        controller = self._loop(rig, oven, OnFault())
        _fault(rig, oven, "zone1", 30, clock)
        assert controller.mode is ControllerMode.REGULATING
        assert rig.stopping.latches.all() == []
        for _ in range(3):
            _good(rig, oven, "zone1", 21.0, clock)
        assert controller.held is None, "resumed by itself"

    def test_manual_latches_the_controller_only(self, rig: Rig, oven: Oven, clock: SteppedClock):
        controller = self._loop(rig, oven, OnFault(FaultAction.MANUAL))
        _fault(rig, oven, "zone1", 1, clock)
        assert controller.mode is ControllerMode.REGULATING, "within its wait (2 s)"
        _fault(rig, oven, "zone1", 2, clock)
        assert controller.mode is ControllerMode.MANUAL
        (event,) = _events(rig, Code.ON_FAULT)
        assert event.details["action"] == "manual"
        rig.write(oven.root, {"h1": 3.0}, writer="program")  # the output is not held
        with pytest.raises(ConflictError, match="on_fault"):
            controller.regulate(25.0)
        assert rig.conditions.get(controller, Code.LATCHED) is not None

    def test_stop_cuts_its_output_only(self, rig: Rig, oven: Oven, clock: SteppedClock):
        rig.attach_controller(oven.signals["h2"], oven.signals["zone2"], law=P(kp=1.0))
        controller = self._loop(rig, oven, OnFault(FaultAction.STOP))
        _set(rig, oven, h2=0.0)
        oven.writes.clear()
        _fault(rig, oven, "zone1", 3, clock)
        assert ("h1", 0.0) in oven.writes
        with pytest.raises(ConflictError, match="reset it to write"):
            _set(rig, oven, h1=10.0)  # a fault's latch refuses even a person
        _set(rig, oven, h2=10.0)  # the next zone is free
        rig.stopping.reset(f"on_fault:{controller.name}", BEN)
        _set(rig, oven, h1=10.0)

    def test_stop_device_latches_the_device(self, rig: Rig, oven: Oven, clock: SteppedClock):
        self._loop(rig, oven, OnFault(FaultAction.STOP_DEVICE))
        _set(rig, oven, h2=40.0)
        oven.writes.clear()
        _fault(rig, oven, "zone1", 3, clock)
        assert sorted(oven.writes) == [("h1", 0.0), ("h2", 0.0)]
        with pytest.raises(ConflictError):
            _set(rig, oven, h3=1.0)

    def test_freeze_then_waits_its_own_time(self, rig: Rig, oven: Oven, clock: SteppedClock):
        controller = self._loop(rig, oven, OnFault(FaultAction.MANUAL, freeze_s=10.0))
        _fault(rig, oven, "zone1", 5, clock)
        _good(rig, oven, "zone1", 21.0, clock)  # a flicker pauses, never resets, the time
        _fault(rig, oven, "zone1", 4, clock)
        assert controller.mode is ControllerMode.REGULATING
        _fault(rig, oven, "zone1", 3, clock)
        assert controller.mode is ControllerMode.MANUAL

    def test_stop_on_an_output_that_keeps_is_refused(self, rig: Rig, oven: Oven):
        with pytest.raises(ConflictError, match="would do nothing"):
            rig.attach_controller(
                oven.signals["h3"], oven.signals["zone1"], on_fault=OnFault(FaultAction.STOP)
            )

    def test_a_latching_fault_interrupts_the_program(self, rig: Rig, oven: Oven, clock):
        programmer = Programmer(rig)
        controller = self._loop(rig, oven, OnFault(FaultAction.MANUAL))
        programmer.start(_prompt())
        _fault(rig, oven, "zone1", 3, clock)
        deadline = time.monotonic() + 5
        while programmer.running and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not programmer.running
        ended = [
            e for e in rig.recent if e.code == Code.INTERRUPTED and e.subject_kind == "program"
        ]
        assert ended and f"fault:{controller.name}" in ended[-1].message

    def test_the_ramp_goes_on_from_the_reading(self, rig: Rig, oven: Oven, clock: SteppedClock):
        controller = rig.attach_controller(oven.signals["h1"], oven.signals["zone1"], law=P(kp=1))
        _good(rig, oven, "zone1", 0.0, clock)
        controller.regulate(0.0, generator=LinearRampSetpoint(Duration(100.0), end=100.0))
        _fault(rig, oven, "zone1", 20, clock)
        for _ in range(3):
            _good(rig, oven, "zone1", 5.0, clock)
        (event,) = _events(rig, Code.RESEEDED)
        assert event.details["end_s"] > event.details["end_was_s"], "it lands later"
        now_s = clock.from_start_s(clock.now_ns())
        assert controller.setpoint_at(clock.now_ns()) == pytest.approx(5.0)
        assert controller.reference.end_time == pytest.approx(now_s + 95.0)  # type: ignore[union-attr]


# endregion

# region The permissive


class TestPermissive:
    def test_refuses_fails_closed_and_permits_off(self, rig: Rig, oven: Oven, clock):
        h1 = oven.signals["h1"]
        rig.permit(h1, Permissive(signal=f"{oven.name}.zone2", above=30.0))
        with pytest.raises(ConflictError, match="needs .* > 30"):
            _set(rig, oven, h1=10.0)
        _set(rig, oven, h1=0.0)  # its stop value is always permitted
        _good(rig, oven, "zone2", 35.0, clock)
        _set(rig, oven, h1=10.0)
        clock.advance(1.0)
        rig.on_samples([Sample(oven.root, clock.now_ns(), {oven.signals["zone2"]: invalid("x")})])
        with pytest.raises(ConflictError, match="fails closed"):
            _set(rig, oven, h1=10.0)

    def test_holds_a_controller(self, rig: Rig, oven: Oven, clock):
        rig.permit(oven.signals["h1"], Permissive(signal=f"{oven.name}.zone2", below=50.0))
        _good(rig, oven, "zone2", 60.0, clock)
        controller = rig.attach_controller(oven.signals["h1"], oven.signals["zone1"], law=P(kp=1))
        _good(rig, oven, "zone1", 20.0, clock)
        controller.regulate(25.0)
        _good(rig, oven, "zone1", 20.0, clock)
        assert controller.held == Code.NOT_PERMITTED
        assert rig.conditions.get(controller, Code.NOT_PERMITTED) is not None

    def test_the_rig_file_key(self):
        with pytest.raises(ValueError, match="above"):
            Permissive(signal="a.b")
        with pytest.raises(ValueError, match="not below"):
            Permissive(signal="a.b", above=5.0, below=1.0)


# endregion

# region Routes


@pytest.fixture
def served(rig: Rig, oven: Oven) -> Iterator[tuple[Rig, Oven]]:
    set_rig(rig)
    set_programmer(Programmer(rig))
    try:
        yield rig, oven
    finally:
        set_programmer(None)
        set_rig(None)


class TestRoutes:
    def test_stop_health_plan_and_reset(self, served):
        rig, oven = served
        with TestClient(create_app()) as http:
            report = http.post("/api/rig/stop", json={"reason": "lid"}).json()
            assert report["devices"][oven.name]["state"] == "stopped"
            health = http.get("/api/health").json()
            assert health["stopped"]["reason"] == "lid"
            assert {"subject_kind": "rig", "subject": "rig", "cause": "stop"} in health["latches"]
            plan = http.get("/api/rig/stop").json()
            assert plan["stopped"]["cause"] == "stop"
            assert {r["address"] for r in plan["outputs"]} == {
                f"{oven.name}.{n}" for n in ("h1", "h2", "h3")
            }
            assert http.get("/api/rig/latches").json()[0]["cause"] == "stop"
            refused = http.put(f"/api/devices/{oven.name}/write", json={"h1": 1.0})
            assert refused.status_code == 200, "an anonymous local person goes through"
            assert http.post("/api/rig/reset", json={"cause": "nope"}).status_code == 404
            assert http.post("/api/rig/reset").status_code == 200
            assert http.get("/api/health").json()["stopped"] is None

    def test_reset_needs_a_person(self, served, monkeypatch):
        rig, _ = served
        _stop(rig)
        monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
        app = create_app(AuthConfig(token="s3cret"))
        with TestClient(app) as http:
            token = http.post("/api/rig/reset", headers={"Authorization": "Bearer s3cret"})
            door = app.state.door
            agent = principal_mod.mint(
                door.key,
                principal_mod.Claims(
                    sub="claude",
                    sid="s9",
                    scp=frozenset({"read", "operate"}),
                    kind="agent",
                    aud=door.aud,
                    cip="",
                    sch="http",
                    iat=int(time.time()),
                    exp=int(time.time()) + 60,
                ),
            )
            by_agent = http.post("/api/rig/reset", headers={principal_mod.HEADER: agent})
        assert token.status_code == 403, token.text
        assert by_agent.status_code == 403, by_agent.text
        assert rig.stopping.latches.rig_stop is not None

    def test_a_person_regulating_clears_a_manual_latch(self, served, clock):
        rig, oven = served
        controller = rig.attach_controller(
            oven.signals["h1"],
            oven.signals["zone1"],
            law=P(kp=1.0),
            on_fault=OnFault(FaultAction.MANUAL),
        )
        controller.regulate(25.0)
        _fault(rig, oven, "zone1", 3, clock)
        for _ in range(3):
            _good(rig, oven, "zone1", 21.0, clock)
        assert controller.mode is ControllerMode.MANUAL
        with TestClient(create_app()) as http:
            out = http.get(f"/api/controllers/{controller.name}").json()
            assert out["latched"] == [f"on_fault:{controller.name}"]
            assert out["on_fault"] == "manual"
            answer = http.post(f"/api/controllers/{controller.name}/regulate", json={"at": 25.0})
        assert answer.status_code == 200, answer.text
        assert controller.mode is ControllerMode.REGULATING


# endregion


# region A blocking device, and the rig file


class SlowOven(Oven):
    """Its commit may wait on a bus: the rig runs it on a writer thread."""

    blocking = True


def test_a_blocking_device_is_stopped_through_its_writer(fresh: Any):
    rig = Rig()
    oven = SlowOven(fresh("slow"))
    rig.add_device(oven)
    report = _stop(rig)
    assert report.devices[oven.name]["state"] == "stopped", report.devices
    assert sorted(oven.writes) == [("h1", 0.0), ("h2", 0.0)], "written before the stop returned"
    rig.close()


SIM = {
    "name": "stopped",
    "links": {"plant": {"type": "sim_plant", "model": "lag", "tau_s": 10.0}},
    "devices": {
        "daq": {
            "driver": "sim_daq",
            "link": "plant",
            "poll_s": 1.0,
            "ports": {"t": {"port": "output", "quantity": "temperature", "unit": "°C"}},
        },
        "drive": {
            "driver": "sim_drive",
            "link": "plant",
            "ports": {"power": "input"},
            "stop": {"power": "keep"},
            "on_shutdown": "keep",
            "permissive": {"power": {"signal": "daq.t", "below": 90.0}},
        },
    },
    "controllers": {
        "drive.power": {
            "measured": "daq.t",
            "law": {"type": "p", "kp": 0.1},
            "on_fault": {"freeze_s": 30.0, "then": "manual"},
        }
    },
}


def test_the_rig_file_keys_load_and_render_back():
    from flyball.runtime.config import RigConfig

    rig = RigConfig.model_validate(SIM).build(start=False)
    power = rig.devices["drive"].signals["power"]
    assert power.spec.off == 0.0, "a linear sim port declares off at limits[0]"
    assert resolve_output(power).origin == "you_said" and resolve_output(power).value is None
    assert rig.entries["drive"].on_shutdown == "keep"
    assert power in rig.permissives
    controller = rig.controllers["drive.power"]
    assert controller.on_fault == OnFault(FaultAction.MANUAL, freeze_s=30.0)
    assert rig.document()["controllers"]["drive.power"]["on_fault"] == {
        "freeze_s": 30.0,
        "then": "manual",
    }
    rig.close()


def test_on_fault_stop_on_a_kept_output_is_refused_at_load():
    from flyball.runtime.config import RigConfig

    document = {
        **SIM,
        "controllers": {"drive.power": {"measured": "daq.t", "on_fault": "stop"}},
    }
    with pytest.raises(ConflictError, match="would do nothing"):
        RigConfig.model_validate(document).build(start=False)


# endregion


class Motor(Committable):
    """No demands: a stop command, and a long move."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.stopped = 0

    @command(long=True)
    def move(self, steps: float) -> None:
        """Move."""
        self.wait(steps)

    @command(stops=True)
    def stop(self) -> None:
        """Stop, release the coils."""
        self.cancel()
        self.stopped += 1


def test_a_device_with_no_demands_is_stopped_by_its_command(fresh: Any):
    rig = Rig()
    motor = Motor(fresh("motor"))
    rig.add_device(motor)
    report = _stop(rig)
    assert motor.stopped == 1 and report.devices[motor.name]["state"] == "stopped"
    with pytest.raises(ConflictError, match="stopped by ben"):
        rig.run_command(motor, "move", {"steps": 0.0})  # automatic: refused
    rig.run_command(motor, "move", {"steps": 0.0}, actor=BEN)  # a person: through


def test_a_planned_stop_writes_the_same_and_latches_nothing(rig: Rig, oven: Oven):
    controller = rig.attach_controller(oven.signals["h1"], oven.signals["zone1"], law=P(kp=1.0))
    controller.regulate(25.0)
    _set(rig, oven, h2=40.0)
    oven.writes.clear()
    report = RigStopper(rig).stop(BEN, "rig edit: added a device", latch=False)
    assert report.latched is False
    assert sorted(oven.writes) == [("h1", 0.0), ("h2", 0.0)]
    assert controller.mode is ControllerMode.MANUAL
    assert rig.stopping.latches.all() == []
    rig.write(oven.root, {"h3": 1.0}, writer="program")  # nothing refuses
