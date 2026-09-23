"""Warning and alarm bands: declared on a signal, set from a rig file, counted when it strays."""

from __future__ import annotations

from pathlib import Path

from flyball_sim.simulation import Simulation

from conftest import TestClient
from flyball.foundation.device import Access, SignalSpec
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius
from flyball.interfaces.server import create_app, set_rig
from flyball.runtime.config import load_rig_config

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"
TEMP = Quantity("temperature", Celsius)


def test_a_signal_spec_carries_bands_and_defaults_to_none():
    plain = SignalSpec(name="t", quantity=TEMP, access=Access.RP, range=(-40.0, 125.0))
    assert plain.warning is None and plain.alarm is None
    banded = SignalSpec(
        name="t", quantity=TEMP, access=Access.RP, warning=(30.0, 90.0), alarm=(10.0, 110.0)
    )
    assert banded.warning == (30.0, 90.0) and banded.alarm == (10.0, 110.0)


def test_oven_rig_file_sets_bands_on_the_thermocouple():
    config = load_rig_config(EXAMPLES / "oven.yaml")
    rig = config.build(start=False)
    signal = rig.resolve("thermocouple.temperature")
    assert signal.spec.range == (0.0, 120.0) and signal.spec.precision == 2
    assert signal.spec.warning == (30.0, 90.0) and signal.spec.alarm == (10.0, 110.0)
    assert signal.spec.quantity == TEMP and str(signal.access) == "rp"


def test_the_bands_fire_on_the_oven_s_readings():
    """Cold (20 °C) is outside `warning`; put the chamber at 5 °C and it is outside `alarm` too."""
    config = load_rig_config(EXAMPLES / "oven.yaml")
    rig = config.build(start=False)
    simulation = Simulation(rig, config)
    signal = rig.resolve("thermocouple.temperature")
    set_rig(rig)
    try:
        with TestClient(create_app()) as client:
            rig.read(signal, fresh=True)
            alarms = client.get("/api/health").json()["alarms"]
            assert alarms == {"warn": 1, "alarm": 0, "unknown": 0, "max_level": 30}
            simulation.reset_plant("chamber", output=5.0)
            rig.read(signal, fresh=True)
            alarms = client.get("/api/health").json()["alarms"]
            assert alarms == {"warn": 0, "alarm": 1, "unknown": 0, "max_level": 40}
            simulation.reset_plant("chamber", output=50.0)
            rig.read(signal, fresh=True)
            # Back inside, but the rig's alarm waits out its hold (2·poll_s) before it clears.
            assert client.get("/api/health").json()["alarms"]["max_level"] == 40
    finally:
        set_rig(None)
