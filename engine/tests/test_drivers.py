"""Drivers from a directory: loaded, reloaded, listed with what each needs; a text link queried."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import TestClient
from flyball.foundation.device import Device, DriverConfig, Input, Readout
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Percent
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.deps import set_drivers_dir
from flyball.rig import Rig
from flyball.runtime.drivers import load_drivers

DRIVER = """
from flyball.foundation.device import DriverConfig, Readout, Readable
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius


class Probe(Readable):
    temperature = Readout("temperature", "Temperature", Quantity("temperature", Celsius))

    def read(self, time_ns, node=None):
        yield self.sample(time_ns, temperature={value})


class ProbeConfig(DriverConfig[Probe], type="test_probe_{n}"):
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
            # A scan is a bus transaction: a POST, so no page on another site can make one. A GET
            # is 405 from the router, or 404 from a built UI's static files -- never the probe.
            got = c.get("/api/probe")
            assert got.status_code in (404, 405) and "flyball-linux" not in got.text
            probed = c.post("/api/probe?scan=false")
            assert probed.status_code == 200 or "flyball-linux" in probed.json()["detail"]
    finally:
        set_drivers_dir(None)
        set_rig(None)


class Blend(Device):
    """Two supplies in, a mix out."""

    dry = Input("dry", "Dry supply", Quantity("humidity", Percent))
    wet = Input("wet")
    mix = Readout("mix", "Mix", Quantity("humidity", Percent))


class BlendConfig(DriverConfig[Blend], type="test_blend"):
    """Mixes two supplies, each another device's signal.

    Nothing to talk to: no link.
    """

    category = "derived"

    def build(self, name: str, label: str | None = None) -> Blend:
        return Blend(name, label)


def test_the_list_says_what_each_driver_requires(_catalog) -> None:
    _catalog.register_device(BlendConfig)
    set_rig(Rig("r"))
    try:
        with TestClient(create_app()) as c:
            listed = c.get("/api/drivers").json()
    finally:
        set_rig(None)
        _catalog.devices.unregister("test_blend")
    blend = listed["test_blend"]
    assert blend["summary"] == "Mixes two supplies, each another device's signal."
    assert blend["category"] == "derived"
    assert blend["requires"] == {
        "link": [],
        "family": None,
        "inputs": [
            {
                "role": "dry",
                "label": "Dry supply",
                "kind": "signal",
                "optional": False,
                "quantity": "humidity",
                "unit": "%",
            },
            {
                "role": "wet",
                "label": "",
                "kind": "signal",
                "optional": False,
                "quantity": "wet",
                "unit": "",
            },
        ],
    }
    assert "addresses" not in blend
    daq = listed["sim_daq"]
    assert daq["requires"]["family"] == "plant" and "sim_plant" in daq["requires"]["link"]
    assert daq["requires"]["inputs"] == [] and "category" not in daq
    assert listed["sim_plant"]["family"] == "plant" and "requires" not in listed["sim_plant"]
    assert listed["scpi"]["requires"]["link"] == ["fake_text", "serial", "visa"]
    assert listed["modbus"]["requires"]["family"] == "modbus"
