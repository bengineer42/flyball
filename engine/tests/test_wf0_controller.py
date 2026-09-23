"""WF0: a held write freezes the law; bounded back-calculation; an unbanked rate window."""

from __future__ import annotations

import math

import pytest
from flyball_sim.clock import SteppedClock

from flyball.control.laws import PI, PID, SmithPredictor
from flyball.foundation.device import (
    Access,
    Committable,
    Demand,
    Readout,
    Role,
    Sample,
    SignalSpec,
)
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Percent, Watt
from flyball.foundation.time import Rate, TimeUnit
from flyball.model.law import ControlLaw, Transfer
from flyball.rig import Rig

TEMP = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)
HUMIDITY = Quantity("humidity", Percent)


class Thermostat(Committable):
    """A heater driven off a zone that goes stale after 5s unread."""

    TREE = (
        SignalSpec(name="zone", quantity=TEMP, access=Access.RP, stale_after_s=5.0),
        SignalSpec(name="heater", quantity=POWER, role=Role.DEMAND, access=Access.RPW),
    )


class Supplied(Committable):
    """A humidity demand bounded by a supply line's reading."""

    supply = Readout("supply", "Supply humidity", HUMIDITY)
    chamber = Readout("chamber", "Chamber humidity", HUMIDITY)
    humidity = Demand("humidity", "Target humidity", HUMIDITY, limits=(0.0, supply))


class Rated(Committable):
    """Demands capped at 10 W/s: one polled every 0.5s, one with no poll period."""

    TREE = (
        SignalSpec(name="zone", quantity=TEMP, access=Access.RP),
        SignalSpec(
            name="polled",
            quantity=POWER,
            role=Role.DEMAND,
            access=Access.RPW,
            max_rate=Rate(10.0, TimeUnit.SECOND),
            poll_s=0.5,
        ),
        SignalSpec(
            name="unpolled",
            quantity=POWER,
            role=Role.DEMAND,
            access=Access.RPW,
            max_rate=Rate(10.0, TimeUnit.SECOND),
        ),
    )


def at(clock: SteppedClock, seconds: float) -> int:
    """Move the clock to `seconds` from its zero; the instant in ns."""
    clock.advance(seconds - clock.now_ns() / 1e9)
    return clock.now_ns()


def _laws() -> list[ControlLaw]:
    return [
        PI(kp=1.0, ki=0.5, tt=2.0),
        PID(kp=1.0, ki=0.5, kd=0.2, tt=2.0),
        SmithPredictor(kp=1.0, ki=0.5, gain=0.1, tau=5.0, dead_time=2.0, tt=2.0),
    ]


class TestAHeldWriteFreezesTheLaw:
    """While the rig would refuse the write, the law does not step; the gap is not integrated."""

    @staticmethod
    def _stale_run(law: ControlLaw, *, hold: bool) -> tuple[Rig, Thermostat, list]:
        clock = SteppedClock(0)
        rig = Rig()
        rig.clock = clock
        dev = Thermostat("thermo")
        rig.add_device(dev)
        zone, heater = dev.signals["zone"], dev.signals["heater"]
        controller = rig.attach_controller(heater, zone, law=law)
        rig.on_samples([Sample(dev.root, at(clock, 10), {zone: 20.0})])
        controller.regulate(30.0, transfer=Transfer.COLD)
        for t in (11, 12):
            rig.on_samples([Sample(dev.root, at(clock, t), {zone: 20.0})])
        frozen = []
        if hold:
            before = (dict(law.state.__dict__), controller.correction, dev.written[heater])
            for t in range(18, 24):  # each reading arrives 5.5s old: past `stale_after_s`
                at(clock, t)
                rig.on_samples([Sample(dev.root, clock.now_ns() - 5_500_000_000, {zone: 20.0})])
                frozen.append(
                    (dict(law.state.__dict__), controller.correction, dev.written[heater]) == before
                )
            rig.on_samples([Sample(dev.root, at(clock, 24), {zone: 22.0})])
        else:
            rig.on_samples([Sample(dev.root, at(clock, 13), {zone: 22.0})])
        return rig, dev, frozen

    @pytest.mark.parametrize("index", range(3), ids=["pi", "pid", "smith"])
    def test_a_stale_source_freezes_the_law_and_recovery_ignores_the_gap(self, index):
        law, reference = _laws()[index], _laws()[index]
        rig, dev, frozen = self._stale_run(law, hold=True)
        ref_rig, ref_dev, _ = self._stale_run(reference, hold=False)
        assert frozen and all(frozen), "state, correction and the applied value unchanged"
        heater = dev.signals["heater"]
        assert dev.written[heater].value == pytest.approx(
            ref_dev.written[ref_dev.signals["heater"]].value
        ), "the first output after the hold is the one without the gap"
        assert law.state == reference.state
        stale = [e for e in rig.recent if e.kind == "stale_input"]
        assert len(stale) == 1, "one event on entering the hold, not one per tick"

    def test_an_unknown_limit_freezes_the_integral(self):
        def run(hold: bool) -> tuple[PI, Supplied, Rig]:
            clock = SteppedClock(0)
            rig = Rig()
            rig.clock = clock
            dev = Supplied("supplied")
            rig.add_device(dev)
            humidity, supply = dev.signals["humidity"], dev.signals["supply"]
            chamber = dev.signals["chamber"]
            law = PI(kp=1.0, ki=0.5, tt=2.0)
            controller = rig.attach_controller(humidity, chamber, law=law)
            rig.on_samples([Sample(dev.root, at(clock, 0), {supply: 95.0, chamber: 40.0})])
            controller.regulate(50.0, transfer=Transfer.COLD)
            for t in (1, 2):
                rig.on_samples([Sample(dev.root, at(clock, t), {chamber: 40.0})])
            if hold:
                rig.on_samples([Sample(dev.root, at(clock, 2.5), {supply: math.nan})])
                integral = law.integral
                for t in (3, 4, 5, 6):
                    rig.on_samples([Sample(dev.root, at(clock, t), {chamber: 40.0})])
                    assert law.integral == integral, f"held at {t}s: the integral does not move"
                rig.on_samples([Sample(dev.root, at(clock, 6.5), {supply: 95.0})])
                rig.on_samples([Sample(dev.root, at(clock, 7), {chamber: 42.0})])
            else:
                rig.on_samples([Sample(dev.root, at(clock, 3), {chamber: 42.0})])
            return law, dev, rig

        held, dev, rig = run(True)
        reference, ref_dev, _ = run(False)
        assert held.state == reference.state
        humidity = dev.signals["humidity"]
        assert dev.written[humidity].value == ref_dev.written[ref_dev.signals["humidity"]].value
        assert [e.kind for e in rig.recent if e.kind.startswith("limit_")] == [
            "limit_unknown",
            "limit_known",
        ]


class TestBackCalculation:
    """B14: the anti-windup term relaxes the output toward what was applied, never past it."""

    @pytest.mark.parametrize("dt", [0.01, 0.5, 2.0, 10.0, 60.0, 600.0])
    def test_the_output_never_crosses_the_applied_value(self, dt):
        law = PI(kp=2.0, ki=0.1, tt=5.0)
        assert law.resume(50.0, 50.0, 96.0) == 96.0, "railed: raw 96, the target took 60"
        output = law.update(dt, 50.0, 50.0, last_applied=60.0)
        assert 60.0 <= output <= 96.0

    def test_a_short_step_matches_the_continuous_rate(self):
        law = PI(kp=2.0, ki=0.1, tt=5.0)
        law.resume(50.0, 50.0, 96.0)
        # d(output)/dt = (applied - raw) / tt = -7.2/s, to first order in dt
        assert law.update(0.001, 50.0, 50.0, last_applied=60.0) == pytest.approx(
            96.0 - 7.2e-3, rel=1e-6
        )


class TestRateWindow:
    """B18: the slew allowance is capped at one update period, so a hold banks none."""

    @pytest.fixture
    def setup(self) -> tuple[Rig, Rated, SteppedClock]:
        clock = SteppedClock(0)
        rig = Rig()
        rig.clock = clock
        dev = Rated("rated")
        rig.add_device(dev)
        return rig, dev, clock

    def test_the_window_is_the_targets_poll_period(self, setup):
        rig, dev, clock = setup
        polled = dev.signals["polled"]
        rig.write(dev.root, {polled: 0.0})
        at(clock, 100)
        assert rig.write(dev.root, {polled: 1000.0})[polled].value == pytest.approx(5.0)

    def test_within_the_window_the_elapsed_time_counts(self, setup):
        rig, dev, clock = setup
        polled = dev.signals["polled"]
        rig.write(dev.root, {polled: 0.0})
        at(clock, 0.2)
        assert rig.write(dev.root, {polled: 1000.0})[polled].value == pytest.approx(2.0)

    def test_without_a_poll_period_a_controllers_min_period(self, setup):
        rig, dev, clock = setup
        unpolled, zone = dev.signals["unpolled"], dev.signals["zone"]
        controller = rig.attach_controller(unpolled, zone, law=PI(kp=1.0), min_period_s=2.0)
        rig.write(dev.root, {unpolled: 0.0}, by=controller)
        at(clock, 100)
        state = rig.write(dev.root, {unpolled: 1000.0}, by=controller)[unpolled]
        assert state.value == pytest.approx(20.0)

    def test_else_one_second(self, setup):
        rig, dev, clock = setup
        unpolled = dev.signals["unpolled"]
        rig.write(dev.root, {unpolled: 0.0})
        at(clock, 100)
        assert rig.write(dev.root, {unpolled: 1000.0})[unpolled].value == pytest.approx(10.0)
