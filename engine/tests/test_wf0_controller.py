"""WF0: a held write freezes the law."""

from __future__ import annotations

import math

import pytest
from flyball_sim.clock import SteppedClock

from flyball.control.laws import PI, PID, SmithPredictor
from flyball.foundation.device import (
    Access,
    Committable,
    Demand,
    Output,
    Role,
    Sample,
    SignalSpec,
)
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Percent, Watt
from flyball.model.law import ControlLaw, Transfer
from flyball.rig import Rig

TEMP = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)
HUMIDITY = Quantity("humidity", Percent)


class Thermostat(Committable):
    """A heater driven off a zone that goes stale after 5s unread."""

    TREE = (
        SignalSpec(name="zone", quantity=TEMP, access=Access.RP, stale_after=5.0),
        SignalSpec(name="heater", quantity=POWER, role=Role.DEMAND, access=Access.RPW),
    )


class Supplied(Committable):
    """A humidity demand bounded by a supply line's reading."""

    supply = Output("supply", "Supply humidity", HUMIDITY)
    chamber = Output("chamber", "Chamber humidity", HUMIDITY)
    humidity = Demand("humidity", "Target humidity", HUMIDITY, limits=(0.0, supply))


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
        controller.regulate(30.0, transfer=Transfer.RESET)
        for t in (11, 12):
            rig.on_samples([Sample(dev.root, at(clock, t), {zone: 20.0})])
        frozen = []
        if hold:
            before = (dict(law.state.__dict__), controller.correction, dev.written[heater])
            for t in range(18, 24):  # each reading arrives 5.5s old: past `stale_after`
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
            controller.regulate(50.0, transfer=Transfer.RESET)
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
