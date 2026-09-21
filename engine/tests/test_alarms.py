"""Warn and alarm bands: declared on a signal, set from a rig file, counted when a value strays."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from flyball_sim.simulation import Simulation

from flyball.core.quantity import Quantity
from flyball.core.signal import Access, SignalSpec
from flyball.core.units.si import Celsius
from flyball.runtime.config import load_rig_config
from flyball.server import create_app, set_rig

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"
TEMP = Quantity("temperature", Celsius)


def test_a_signal_spec_carries_bands_and_defaults_to_none():
    plain = SignalSpec(name="t", quantity=TEMP, access=Access.RP, range=(-40.0, 125.0))
    assert plain.warn is None and plain.alarm is None
    banded = SignalSpec(
        name="t", quantity=TEMP, access=Access.RP, warn=(30.0, 90.0), alarm=(10.0, 110.0)
    )
    assert banded.warn == (30.0, 90.0) and banded.alarm == (10.0, 110.0)


def test_oven_rig_file_sets_bands_on_the_thermocouple():
    config = load_rig_config(EXAMPLES / "oven.yaml")
    rig = config.build(start=False)
    signal = rig.resolve("thermocouple.temperature")
    assert signal.spec.range == (0.0, 120.0) and signal.spec.precision == 2
    assert signal.spec.warn == (30.0, 90.0) and signal.spec.alarm == (10.0, 110.0)
    assert signal.spec.quantity == TEMP and str(signal.access) == "rp"


def test_the_bands_fire_on_the_oven_s_readings():
    """Cold (20 °C) is outside `warn`; put the chamber at 5 °C and it is outside `alarm` too."""
    config = load_rig_config(EXAMPLES / "oven.yaml")
    rig = config.build(start=False)
    simulation = Simulation(rig, config)
    signal = rig.resolve("thermocouple.temperature")
    set_rig(rig)
    try:
        with TestClient(create_app()) as client:
            rig.read(signal, fresh=True)
            alarms = client.get("/api/health").json()["alarms"]
            assert alarms == {"warn": 1, "alarm": 0, "max_level": 30}
            simulation.reset_plant("chamber", output=5.0)
            rig.read(signal, fresh=True)
            alarms = client.get("/api/health").json()["alarms"]
            assert alarms == {"warn": 0, "alarm": 1, "max_level": 40}
            simulation.reset_plant("chamber", output=50.0)
            rig.read(signal, fresh=True)
            assert client.get("/api/health").json()["alarms"]["max_level"] == 0
    finally:
        set_rig(None)
