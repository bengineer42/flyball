"""Building a rig up while it runs: links, devices, documents, versions, saving."""

from __future__ import annotations

import pytest
from flyball_sim import SteppedClock

from conftest import FakeRunner, TestClient
from flyball.foundation.config import Config
from flyball.foundation.files import load_document
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.deps import set_runner, set_store
from flyball.record.sqlite import SqliteStore
from flyball.rig import Rig
from flyball.runtime.config import RunnerConfig


class _RealLinkConfig(Config[object], tag="test_real_link"):
    """A stand-in for any real (non-fake, non-sim) link's own config.

    Only `config_tag`'s prefix matters to `is_simulated`/the hardware gate --
    `flyball.hardware.links` has no config classes of its own any more, the
    real link kinds (`visa`, `modbus_tcp`, ...) live in extensions/*.
    """

    def build(self) -> object:
        return object()


PLANT = {"name": "t1", "tag": "sim_plant", "model": "lag", "tau_s": 1.0, "gain": 1.0}
DAQ = {
    "name": "probe",
    "driver": "sim_daq",
    "config": {"link": "t1", "ports": {"signal": {"port": "output", "quantity": "x", "unit": "1"}}},
    "poll_s": 0.1,
}
DRIVE = {"name": "drive", "driver": "sim_drive", "config": {"link": "t1", "ports": {"u": "input"}}}
CONTROLLER = {"drive.u": {"measured": "probe.signal", "law": {"tag": "P", "kp": 0.5}}}


def entry(posted: dict) -> dict:
    """A posted body as the file's entry: without its name."""
    return {k: v for k, v in posted.items() if k != "name"}


def link_config(posted: dict):
    from flyball.runtime.config import RigConfig

    return RigConfig.model_validate({"links": {posted["name"]: entry(posted)}}).links[
        posted["name"]
    ]


def device_entry(posted: dict, **more):
    from flyball.foundation.device import DeviceEntry

    return DeviceEntry.model_validate({**entry(posted), **more})


@pytest.fixture
def rig() -> Rig:
    rig = Rig("bare")
    rig.clock = SteppedClock(0)
    rig.loaded = rig.document()  # as a start does: what `changes` are measured from
    return rig


@pytest.fixture
def store(tmp_path) -> SqliteStore:
    return SqliteStore(tmp_path / "rig.sqlite")


@pytest.fixture
def client(rig, store):
    versions: list[str] = []
    rig.on_change = lambda reason: (
        versions.append(reason),
        store.save_rig_version(rig.clock.now_ns(), reason, rig.document()),
    )
    store.save_rig_version(0, "started bare", rig.document())
    set_rig(rig)
    set_store(store)
    with TestClient(create_app()) as c:
        c.versions = versions  # type: ignore[attr-defined]
        yield c
    set_runner(None)
    set_store(None)
    set_rig(None)
    rig.close()


class TestRig:
    def test_a_rig_is_built_up_and_rendered_back(self, rig: Rig) -> None:
        rig.add_link("t1", link_config(PLANT))
        probe = rig.add_entry("probe", device_entry(DAQ))
        drive = rig.add_entry("drive", device_entry(DRIVE))
        rig.attach_controller(drive.signals["u"], probe.signals["signal"])
        document = rig.document()
        assert set(document["links"]) == {"t1"} and set(document["devices"]) == {"probe", "drive"}
        assert document["controllers"]["drive.u"]["measured"] == "probe.signal"
        assert "probe" in rig.polling.by_name, "polled on its period"
        rig.remove_device("probe")
        assert "probe" not in rig.devices and "probe" not in rig.polling.by_name
        assert list(rig.controllers) == [], "the controller regulating it went with it"
        assert "drive.u" not in rig.document()["controllers"]
        with pytest.raises(Exception, match="used by"):
            rig.remove_link("t1")
        rig.remove_device("drive")
        rig.remove_link("t1")
        assert rig.document()["links"] == {} and rig.document()["devices"] == {}

    def test_an_input_bound_into_a_removed_device_is_unbound(self, rig: Rig) -> None:
        rig.add_link("t1", link_config(PLANT))
        rig.add_entry("probe", device_entry(DAQ))
        follower = rig.add_entry("follower", device_entry(DRIVE, bound={"x": "probe.signal"}))
        assert follower.bound["x"].address == "probe.signal"
        rig.remove_device("probe")
        assert follower.bound == {}


class TestRoutes:
    def test_links_devices_and_a_controller_one_at_a_time(
        self, client: TestClient, rig: Rig
    ) -> None:
        assert client.post("/api/links", json=PLANT).status_code == 201
        assert client.post("/api/links", json=PLANT).status_code == 409
        r = client.post("/api/devices", json=DAQ)
        assert r.status_code == 201, r.text
        assert r.json()["name"] == "probe" and r.json()["readable"]
        assert client.post("/api/devices", json=DRIVE).status_code == 201
        assert (
            client.post("/api/devices", json={**DAQ, "name": "x", "driver": "no_such"}).status_code
            == 422
        )
        assert (
            client.post(
                "/api/devices",
                json={**DAQ, "name": "y", "config": {**DAQ["config"], "link": "nope"}},
            ).status_code
            == 404
        )
        r = client.post(
            "/api/controllers",
            json={"output": "drive.u", "measured": "probe.signal", "law": {"tag": "P", "kp": 0.5}},
        )
        assert r.status_code == 201, r.text
        document = client.get("/api/rig/document").json()
        assert (
            set(document["devices"]) == {"probe", "drive"} and "drive.u" in document["controllers"]
        )
        assert client.versions == [
            "added link t1",
            "added device probe",
            "added device drive",
            "attached controller drive.u",
        ]  # type: ignore[attr-defined]
        assert client.delete("/api/links/t1").status_code == 409
        assert client.delete("/api/devices/probe").status_code == 204
        assert client.delete("/api/devices/probe").status_code == 404
        assert client.get("/api/rig/document").json()["controllers"] == {}

    def test_a_whole_document_versions_changes_and_restore(
        self, client: TestClient, rig: Rig, store: SqliteStore
    ) -> None:
        document = {
            "links": {"t1": {k: v for k, v in PLANT.items() if k != "name"}},
            "devices": {
                "probe": {k: v for k, v in DAQ.items() if k != "name"},
                "drive": {k: v for k, v in DRIVE.items() if k != "name"},
            },
            "controllers": CONTROLLER,
        }
        r = client.post("/api/rig", json=document)
        assert r.status_code == 201, r.text
        assert set(rig.devices) == {"probe", "drive"} and "drive.u" in rig.controllers
        versions = client.get("/api/rig/versions").json()
        assert [v["reason"] for v in versions][-1] == "started bare"
        assert versions[0]["reason"] == "attached controller drive.u"
        changes = client.get("/api/rig/changes").json()
        assert set(changes) == {"links", "devices", "controllers"}
        first = versions[-1]["id"]
        r = client.post(f"/api/rig/versions/{first}/restore")
        assert r.status_code == 200, r.text
        assert rig.devices == {} and list(rig.controllers) == [] and rig.links == {}
        assert client.get("/api/rig/changes").json() == {}
        restored = [e for e in rig.recent if e.kind == "restored"]
        assert len(restored) == 1, "a restore is recorded on the rig's event stream"
        assert restored[0].scope == "rig" and restored[0].details == {"version_id": first}
        after = client.get("/api/rig/versions").json()
        assert len(after) == len(versions), "a restore writes no version"
        assert [v["id"] for v in after if v["head"]] == [first], "the head moved to it"
        assert [v["parent"] for v in after][::-1] == [None, *(v["id"] for v in after[::-1][:-1])]
        # A change after a restore branches from what was restored.
        assert client.post("/api/links", json=PLANT).status_code == 201
        branch = client.get("/api/rig/versions").json()[0]
        assert branch["parent"] == first and branch["head"]
        full = client.get(f"/api/rig/versions/{first + 4}").json()["document"]
        r = client.post(f"/api/rig/versions/{first + 4}/restore")
        assert r.status_code == 200 and set(rig.devices) == {"probe", "drive"}
        assert client.get("/api/rig/document").json()["devices"] == full["devices"]
        assert client.get(f"/api/rig/versions/{first + 4}").json()["head"] is True

    def test_save_writes_an_overlay_by_default_and_a_whole_rig_to_a_path(
        self, client: TestClient, rig: Rig, tmp_path
    ) -> None:
        assert client.post("/api/rig/save").status_code == 409, "not started from a file: say where"
        rig_file = tmp_path / "lab.yaml"
        rig_file.write_text("name: lab\n")
        rig.files = [rig_file]
        assert client.post("/api/links", json=PLANT).status_code == 201
        assert client.post("/api/devices", json=DAQ).status_code == 201
        r = client.post("/api/rig/save")
        assert r.status_code == 200, r.text
        written = tmp_path / "lab.yaml.d" / "added.yaml"
        assert r.json()["path"] == str(written)
        overlay = load_document(written)
        assert set(overlay) == {"links", "devices"} and "probe" in overlay["devices"]
        r = client.post("/api/rig/save", json={"path": str(tmp_path / "whole.yaml")})
        assert r.status_code == 409 and "--allow-save" in r.json()["detail"]
        set_runner(FakeRunner(RunnerConfig(allow_save=True)))
        assert client.post("/api/rig/save", json={"path": str(rig_file)}).status_code == 409
        r = client.post("/api/rig/save", json={"path": str(tmp_path / "whole.yaml")})
        assert r.status_code == 200
        whole = load_document(tmp_path / "whole.yaml")
        assert whole["name"] == "bare" and set(whole["devices"]) == {"probe"}
        assert (
            client.post("/api/rig/save", json={"path": str(tmp_path / "x.txt")}).status_code == 422
        )

    def test_overwriting_a_rig_file_keeps_its_runner_section(
        self, client: TestClient, rig: Rig, tmp_path
    ) -> None:
        # The runner section is not part of the rig, so `rig.document()` has none; a save over
        # the file must not drop it, or the password goes and the next start is open.
        base = tmp_path / "base.yaml"
        base.write_text("runner:\n  auth:\n    token: base-token\n  allow_save: true\n")
        rig_file = tmp_path / "lab.yaml"
        rig_file.write_text(
            "name: lab\nextends: [base.yaml]\nrunner:\n  auth:\n    password: hunter2\n"
        )
        rig.files = [rig_file]
        set_runner(FakeRunner(RunnerConfig(allow_save=True)))
        assert client.post("/api/links", json=PLANT).status_code == 201
        r = client.post("/api/rig/save", json={"path": str(rig_file), "overwrite": True})
        assert r.status_code == 200, r.text
        assert "runner" not in r.json()["document"], "the secrets are not sent back"
        saved = load_document(rig_file)
        assert saved["runner"] == {
            "auth": {"token": "base-token", "password": "hunter2"},
            "allow_save": True,
        }, "the file's own runner section, with what it extended, is kept"
        assert "t1" in saved["links"]
        # Any existing file is overwritten the same way; a new one has no runner section.
        other = tmp_path / "other.yaml"
        other.write_text("runner:\n  auth:\n    token: other-token\n")
        assert client.post("/api/rig/save", json={"path": str(other)}).status_code == 200
        assert load_document(other)["runner"] == {"auth": {"token": "other-token"}}
        fresh = tmp_path / "fresh.yaml"
        assert client.post("/api/rig/save", json={"path": str(fresh)}).status_code == 200
        assert "runner" not in load_document(fresh)
        # A file whose runner section cannot be read is not overwritten blind.
        broken = tmp_path / "broken.yaml"
        broken.write_text("runner: [unclosed\n")
        r = client.post("/api/rig/save", json={"path": str(broken)})
        assert r.status_code == 409 and "runner" in r.json()["detail"]
        assert broken.read_text() == "runner: [unclosed\n"


class TestHardwareGate:
    def test_a_hardware_rig_composes_only_with_the_flag(
        self, client: TestClient, rig: Rig, _catalog
    ) -> None:
        from flyball.interfaces.server.deps import set_compose

        _catalog.links.register(_RealLinkConfig)
        # A bare rig may always be built up, even with a real link: that is what it is for.
        rig.link_entries["dmm"] = _RealLinkConfig()
        rig.links["dmm"] = object()
        off = {"detail": "Composition is off on a hardware rig: start the runner with --compose"}
        assert client.post("/api/links", json=PLANT).json() == off
        assert client.post("/api/devices", json=DAQ).status_code == 409
        assert client.delete("/api/links/dmm").status_code == 409
        assert client.post("/api/rig", json={"links": {"t1": entry(PLANT)}}).status_code == 409
        assert client.get("/api/rig/document").status_code == 200, "reading is always allowed"
        set_compose(True)
        try:
            assert client.post("/api/links", json=PLANT).status_code == 201
            assert client.post("/api/devices", json=DAQ).status_code == 201
        finally:
            set_compose(False)


def test_a_batch_read_gives_null_for_a_signal_never_read(client: TestClient, rig: Rig) -> None:
    assert client.post("/api/links", json=PLANT).status_code == 201
    assert client.post("/api/devices", json=DRIVE).status_code == 201
    assert client.post("/api/devices", json=DAQ).status_code == 201
    got = client.get("/api/read", params={"at": "drive.conditions,drive.u,probe.conditions"}).json()
    assert got[0] is not None and got[1] is None and got[2] is not None
