"""Drivers from a directory: loaded, reloaded, listed; a text link queried."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flyball.core.config import Config
from flyball.hardware.links import FakeTextLink
from flyball.runtime.drivers import load_drivers
from flyball.runtime.rig import Rig
from flyball.server import create_app, set_rig
from flyball.server.deps import set_drivers_dir

DRIVER = """
from flyball.core.device import DriverConfig, Output, Readable
from flyball.core.quantity import Quantity
from flyball.core.units.si import Celsius


class Probe(Readable):
    temperature = Output("temperature", "Temperature", Quantity("temperature", Celsius))

    def read(self, time_ns, node=None):
        yield self.sample(time_ns, temperature={value})


class ProbeConfig(DriverConfig[Probe], tag="test_probe_{n}"):
    def build(self, name, label=None):
        return Probe(name, label)
"""


@pytest.fixture
def drivers(tmp_path) -> Path:
    directory = tmp_path / "drivers"
    directory.mkdir()
    (directory / "probe.py").write_text(DRIVER.format(value=20.0, n=1))
    (directory / "broken.py").write_text("import nothing_of_the_sort\n")
    yield directory
    Config.registry.pop("test_probe_1", None)
    Config.registry.pop("test_probe_2", None)


def test_a_directory_of_drivers_loads_and_reloads(drivers: Path) -> None:
    report = load_drivers(drivers)
    assert report.registered == {"probe": ["test_probe_1"]}
    assert "broken" in report.errors and "ModuleNotFoundError" in report.errors["broken"]
    first = Config.registry["test_probe_1"]
    (drivers / "probe.py").write_text(DRIVER.format(value=21.0, n=1))  # edited: same tag
    report = load_drivers(drivers)
    assert report.registered["probe"] == ["test_probe_1"], "re-registered without a clash"
    assert Config.registry["test_probe_1"] is not first
    assert load_drivers(drivers / "missing").registered == {}


def test_the_routes_list_reload_and_query(drivers: Path) -> None:
    rig = Rig("r")
    rig.links["dmm"] = FakeTextLink({"*IDN?": "ACME,DMM,1"})
    rig.links["plain"] = object()
    set_rig(rig)
    set_drivers_dir(drivers)
    try:
        with TestClient(create_app()) as c:
            listed = c.get("/api/drivers").json()
            assert "sim_daq" in listed and listed["sim_daq"]["role"] == "driver"
            assert "sim_plant" in listed and listed["sim_plant"]["role"] == "link"
            assert "properties" in listed["sim_daq"]["schema"]
            r = c.post("/api/drivers/reload")
            assert r.status_code == 200 and r.json()["registered"] == {"probe": ["test_probe_1"]}
            assert "broken" in r.json()["errors"]
            assert "test_probe_1" in c.get("/api/drivers").json()
            r = c.post("/api/links/dmm/query", json={"text": "*IDN?"})
            assert r.status_code == 200 and r.json() == {"reply": "ACME,DMM,1"}
            assert c.post("/api/links/plain/query", json={"text": "x"}).status_code == 409
            assert c.post("/api/links/nope/query", json={"text": "x"}).status_code == 404
            assert c.get("/api/probe").status_code in (200, 404)  # flyball-linux may not be here
    finally:
        set_drivers_dir(None)
        set_rig(None)
