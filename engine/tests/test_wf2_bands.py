"""Signal faults stage 1b: a reading outside a signal's band is a condition on the signal.

`band_warning` / `band_alarm`, raised at once, cleared with hysteresis
(`max(2·poll_s, 1 s)` back inside), one at a time; counted by `/api/health`.
"""

from __future__ import annotations

import math

from flyball_sim import SteppedClock

from conftest import TestClient
from flyball.foundation.device import Code, Edge, Sample, Severity
from flyball.interfaces.server import create_app, set_rig
from flyball.rig import Rig
from test_rig_devices import Furnace

WARNING = (10.0, 50.0)
ALARM = (0.0, 80.0)
BANDS = {Code.BAND_WARNING, Code.BAND_ALARM}


def _rig() -> tuple[Rig, Furnace]:
    rig = Rig("bands")
    rig.clock = SteppedClock(0)
    furnace = Furnace("furnace")
    for name in ("zone1", "zone2"):
        furnace.signals[name].set_meta(warning=WARNING, alarm=ALARM)
    rig.add_device(furnace)
    return rig, furnace


def _push(rig: Rig, furnace: Furnace, value: float, name: str = "zone1") -> None:
    signal = furnace.signals[name]
    rig.on_samples([Sample(furnace.root, rig.clock.now_ns(), {signal: value})])


def _edges(rig: Rig) -> list[tuple[str, str, str]]:
    """Every band edge so far: `(code, edge, subject)`."""
    return [(e.code, str(e.edge), e.subject) for e in rig.recent if e.code in BANDS]


def _held(rig: Rig, furnace: Furnace, name: str = "zone1") -> list[str]:
    return [c.code for c in rig.conditions.of(furnace.signals[name])]


def test_a_crossing_raises_once_at_once_with_side_value_and_bounds():
    rig, furnace = _rig()
    _push(rig, furnace, 30.0)
    assert _edges(rig) == []
    _push(rig, furnace, 60.0)
    assert _edges(rig) == [("band_warning", "raised", "furnace.zone1")]
    for value in (61.0, 70.0, 55.0):
        rig.clock.advance(0.5)
        _push(rig, furnace, value)
    assert _edges(rig) == [("band_warning", "raised", "furnace.zone1")], "once, not per reading"
    (condition,) = rig.conditions.of(furnace.signals["zone1"])
    assert condition.code == Code.BAND_WARNING and condition.severity == Severity.WARNING
    assert condition.subject_kind == "signal" and condition.subject == "furnace.zone1"
    assert condition.details == {"side": "high", "value": 60.0, "bounds": [10.0, 50.0]}
    _push(rig, furnace, 5.0, "zone2")
    (low,) = rig.conditions.of(furnace.signals["zone2"])
    assert low.details == {"side": "low", "value": 5.0, "bounds": [10.0, 50.0]}


def test_hovering_at_the_edge_does_not_flap_and_clears_after_the_hold():
    rig, furnace = _rig()
    for value in (50.5, 49.5, 50.5, 49.5, 50.5, 49.5):
        _push(rig, furnace, value)
        rig.clock.advance(0.3)
    assert _edges(rig) == [("band_warning", "raised", "furnace.zone1")]
    # 49.5 has been inside since the last push; hold is 1 s (Furnace has no poll_s).
    _push(rig, furnace, 49.0)
    rig.clock.advance(0.6)
    _push(rig, furnace, 48.0)
    assert _held(rig, furnace) == ["band_warning"], "0.9 s back inside is not enough"
    rig.clock.advance(0.2)
    _push(rig, furnace, 48.0)
    assert _held(rig, furnace) == []
    assert _edges(rig) == [
        ("band_warning", "raised", "furnace.zone1"),
        ("band_warning", "cleared", "furnace.zone1"),
    ]


def test_a_break_in_the_run_back_inside_restarts_the_hold():
    rig, furnace = _rig()
    _push(rig, furnace, 60.0)
    rig.clock.advance(0.1)
    _push(rig, furnace, 40.0)
    rig.clock.advance(0.8)
    _push(rig, furnace, 60.0)  # beyond again: the run inside is broken
    rig.clock.advance(0.1)
    _push(rig, furnace, 40.0)
    rig.clock.advance(0.8)
    _push(rig, furnace, 40.0)
    assert _held(rig, furnace) == ["band_warning"]
    rig.clock.advance(0.2)
    _push(rig, furnace, 40.0)
    assert _held(rig, furnace) == []


def test_the_hold_is_twice_poll_s_when_that_is_longer():
    rig, furnace = _rig()
    signal = furnace.signals["zone1"]
    signal.set_meta(poll_s=2.0)
    _push(rig, furnace, 60.0)
    _push(rig, furnace, 40.0)
    rig.clock.advance(3.9)
    _push(rig, furnace, 40.0)
    assert _held(rig, furnace) == ["band_warning"]
    rig.clock.advance(0.1)
    _push(rig, furnace, 40.0)
    assert _held(rig, furnace) == []


def test_alarm_supersedes_warning_and_swaps_back_after_the_hold():
    rig, furnace = _rig()
    _push(rig, furnace, 60.0)
    rig.clock.advance(0.1)
    _push(rig, furnace, 90.0)
    assert _held(rig, furnace) == ["band_alarm"], "at most one: the alarm replaces the warning"
    (condition,) = rig.conditions.of(furnace.signals["zone1"])
    assert condition.severity == Severity.ERROR
    assert condition.details == {"side": "high", "value": 90.0, "bounds": [0.0, 80.0]}
    rig.clock.advance(0.1)
    _push(rig, furnace, 60.0)  # back to warning: the alarm waits out the hold
    assert _held(rig, furnace) == ["band_alarm"]
    rig.clock.advance(1.0)
    _push(rig, furnace, 65.0)
    assert _held(rig, furnace) == ["band_warning"]
    rig.clock.advance(1.0)
    _push(rig, furnace, 30.0)
    rig.clock.advance(1.0)
    _push(rig, furnace, 30.0)
    assert _held(rig, furnace) == []
    assert [(code, edge) for code, edge, _ in _edges(rig)] == [
        ("band_warning", "raised"),
        ("band_alarm", "raised"),
        ("band_warning", "cleared"),
        ("band_warning", "raised"),
        ("band_alarm", "cleared"),
        ("band_warning", "cleared"),
    ]


def test_a_fall_from_alarm_straight_inside_clears_the_alarm_alone():
    rig, furnace = _rig()
    _push(rig, furnace, 95.0)
    _push(rig, furnace, 30.0)
    rig.clock.advance(1.0)
    _push(rig, furnace, 30.0)
    assert [(code, edge) for code, edge, _ in _edges(rig)] == [
        ("band_alarm", "raised"),
        ("band_alarm", "cleared"),
    ]


def test_nan_is_no_value_and_breaks_the_run_back_inside():
    rig, furnace = _rig()
    _push(rig, furnace, math.nan)
    _push(rig, furnace, math.inf)
    assert _edges(rig) == [] and _held(rig, furnace) == []
    _push(rig, furnace, 60.0)
    _push(rig, furnace, 40.0)
    rig.clock.advance(0.5)
    _push(rig, furnace, math.nan)  # invalid: the band is unknown, the held one stays
    assert _held(rig, furnace) == ["band_warning"]
    rig.clock.advance(0.5)
    _push(rig, furnace, 40.0)  # back inside starts again after the break
    assert _held(rig, furnace) == ["band_warning"]
    rig.clock.advance(1.0)
    _push(rig, furnace, 40.0)
    assert _held(rig, furnace) == []


def test_a_signal_without_bands_raises_nothing():
    rig, furnace = _rig()
    _push(rig, furnace, 1e6, "sample")
    assert _edges(rig) == []


def test_removing_the_device_clears_its_band_conditions():
    rig, furnace = _rig()
    _push(rig, furnace, 60.0)
    _push(rig, furnace, 90.0, "zone2")
    rig.remove_device("furnace")
    assert [c for c in rig.conditions.all() if c.code in BANDS] == []
    cleared = [e for e in rig.recent if e.code in BANDS and e.edge == Edge.CLEARED]
    assert {e.subject for e in cleared} == {"furnace.zone1", "furnace.zone2"}
    assert all(e.details["reason"] == "removed" for e in cleared)


def test_removing_a_band_clears_its_condition_at_once():
    rig, furnace = _rig()
    _push(rig, furnace, 90.0)
    signal = furnace.signals["zone1"]
    signal.set_meta(warning=None)
    assert _held(rig, furnace) == ["band_alarm"], "the alarm band is still there"
    signal.set_meta(alarm=None)
    assert _held(rig, furnace) == []
    assert _edges(rig)[-1] == ("band_alarm", "cleared", "furnace.zone1")
    _push(rig, furnace, 90.0)
    assert _held(rig, furnace) == []


def test_health_counts_band_conditions_only():
    rig, furnace = _rig()
    set_rig(rig)
    try:
        with TestClient(create_app()) as client:
            health = client.get("/api/health").json()
            assert health["alarms"] == {"warn": 0, "alarm": 0, "unknown": 0, "max_level": 0}
            _push(rig, furnace, 60.0)
            _push(rig, furnace, 90.0, "zone2")
            health = client.get("/api/health").json()
            assert health["alarms"] == {"warn": 1, "alarm": 1, "unknown": 0, "max_level": 40}
            assert health["ok"] is True, "a band alarm is not a fault"
            codes = {(c["subject"], c["code"]) for c in health["conditions"]}
            assert codes == {("furnace.zone1", "band_warning"), ("furnace.zone2", "band_alarm")}
            # A fault condition is a fault, not an alarm.
            furnace.set_condition(Code.OFFLINE, Severity.ERROR, "gone")
            health = client.get("/api/health").json()
            assert health["alarms"] == {"warn": 1, "alarm": 1, "unknown": 0, "max_level": 40}
            assert health["ok"] is False
            # The device's own view carries its signals' band conditions.
            (device,) = [d for d in client.get("/api/devices").json() if d["name"] == "furnace"]
            held = {(c["subject_kind"], c["subject"], c["code"]) for c in device["conditions"]}
            assert ("signal", "furnace.zone2", "band_alarm") in held
    finally:
        set_rig(None)
