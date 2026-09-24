"""A `sim_drive` refuses two demands on one plant port at build (D17 item 3)."""

from __future__ import annotations

import pytest

from flyball_sim import DrivePort, PlantConfig, SimDriveConfig


def test_two_demands_on_one_port_are_refused() -> None:
    plant = PlantConfig(model="lag", gain=10.0)
    spelled = DrivePort(port="input", quantity="power", unit="W", limits=(0.0, 100.0))
    with pytest.raises(ValueError, match="'drive' and 'power'|'power' and 'drive'"):
        SimDriveConfig(link=plant, ports={"drive": "input", "power": spelled}).build("heater")


def test_one_demand_per_port_builds() -> None:
    plant = PlantConfig(model="lag", gain=10.0)
    drive = SimDriveConfig(link=plant, ports={"drive": "input"}).build("heater")
    assert drive.ports == {"drive": "input"}
