"""Drivers from a directory: loaded, reloaded, listed; a text link queried."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import TestClient
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.deps import set_drivers_dir
from flyball.rig import Rig
from flyball.runtime.drivers import load_drivers

DRIVER = """
from flyball.foundation.device import DriverConfig, Output, Readable
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius


class Probe(Readable):
    temperature = Output("temperature", "Temperature", Quantity("temperature", Celsius))

    def read(self, time_ns, node=None):
        yield self.sample(time_ns, temperature={value})


class ProbeConfig(DriverConfig[Probe], tag="test_probe_{n}"):
    def build(self, name, label=None):
        return Probe(name, label)
"""


@pytest.fixture
def drivers(tmp_path, _catalog) -> Path:
    directory = tmp_path / "drivers"
    directory.mkdir()
    (directory / "probe.py").write_text(DRIVER.format(value=20.0, n=1))
    (directory / "broken.py").write_text("import nothing_of_the_sort\n")
    yield directory
    _catalog.devices.unregister("test_probe_1")
    _catalog.devices.unregister("test_probe_2")


def test_a_directory_of_drivers_loads_and_reloads(drivers: Path, _catalog) -> None:
    report = load_drivers(drivers)
    assert report.registered == {"probe": ["test_probe_1"]}
    assert "broken" in report.errors and "ModuleNotFoundError" in report.errors["broken"]
    first = _catalog.devices["test_probe_1"]
    (drivers / "probe.py").write_text(DRIVER.format(value=21.0, n=1))  # edited: same tag
    report = load_drivers(drivers)
    assert report.registered["probe"] == ["test_probe_1"], "re-registered without a clash"
    assert _catalog.devices["test_probe_1"] is not first
    assert load_drivers(drivers / "missing").registered == {}


class _FakeTextLink:
    """A minimal stand-in for a text link: `/api/links/{name}/query` only needs `query()`.

    `flyball.hardware.links` has no `FakeTextLink` of its own any more -- the
    scripted fake (and every real text-link kind) lives in extensions/visa.
    """

    def __init__(self, replies: dict[str, str]) -> None:
        self.replies = replies

    def query(self, command: str) -> str:
        return self.replies[command]


def test_the_routes_list_reload_and_query(drivers: Path) -> None:
    rig = Rig("r")
    rig.links["dmm"] = _FakeTextLink({"*IDN?": "ACME,DMM,1"})
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
