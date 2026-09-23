"""examples/scenarios and examples/fault-fixtures build against real drivers and their sim overlay.

These live outside this package (`examples/` at the repo root, not
`extensions/linux/examples/`), but they use `flyball_linux`/`flyball_chips`
drivers, so this is where they get exercised. `rig.yaml` alone is only
*parsed*, not built -- opening the real link needs real hardware (an i2c
bus, a UART, a pwm chip); only the `sim.yaml` overlay is actually built.
"""

from pathlib import Path

import pytest
from flyball.runtime.config import load_rig_config

ROOT = Path(__file__).resolve().parents[3]
SCENARIOS = ROOT / "examples" / "scenarios"
FAULT_FIXTURES = ROOT / "examples" / "fault-fixtures"


@pytest.mark.parametrize("name", ["aging-room", "mushroom-room"])
def test_scenario_sim_overlay_builds(name: str) -> None:
    # Unlike extensions/linux/examples/greenhouse.yaml's overlay (real driver
    # kept, only the link swapped for a fake), each scenario's sim.yaml
    # swaps in a generic sim_daq/sim_drive under the same device names --
    # a stand-in room, not the real driver on a fake bus -- so only the
    # addresses, not the drivers, are expected to match.
    directory = SCENARIOS / name
    real = load_rig_config(directory / "rig.yaml")
    sim_config = load_rig_config([directory / "rig.yaml", directory / "sim.yaml"])
    assert sim_config.simulated
    assert set(sim_config.devices) == set(real.devices)
    sim = sim_config.build(start=False)
    assert set(sim.devices) == set(real.devices)


@pytest.mark.xfail(strict=True, reason="EX-02: pump_pwm's sim overlay leaves its pwm link unresolved")
def test_dosing_skid_sim_overlay_builds() -> None:
    directory = SCENARIOS / "dosing-skid"
    load_rig_config([directory / "rig.yaml", directory / "sim.yaml"]).build(start=False)


@pytest.mark.parametrize("name", ["wiring-mismatch", "calibration-mismatch"])
def test_fault_fixture_builds(name: str) -> None:
    rig = load_rig_config(FAULT_FIXTURES / name / "rig.yaml").build(start=False)
    assert rig.devices
