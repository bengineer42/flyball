"""Changing a rig: links, devices, documents and versions, saved and restarted (D-051); saving."""

from __future__ import annotations

import pytest
import yaml
from flyball_sim import SteppedClock

from conftest import FakeRunner, TestClient
from flyball.foundation.config import Config
from flyball.foundation.device import Device, DriverConfig, Setting, command
from flyball.foundation.files import load_document
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Hertz
from flyball.interfaces.server import create_app, set_programmer, set_rig
from flyball.interfaces.server.deps import current_rig, set_runner, set_store
from flyball.model.controller import ControllerMode
from flyball.record.sqlite import SqliteStore
from flyball.rig import Rig
from flyball.runner.starting import keep_versions, resumed
from flyball.runtime import edits
from flyball.runtime.config import RunnerConfig, load_rig_config
from flyball.runtime.edits import Origin


class _RealLinkConfig(Config[object], type="test_real_link"):
    """A stand-in for any real (non-fake, non-sim) link's own config.

    Only `type_name`'s prefix matters to `is_simulated`/the hardware gate --
    `flyball.hardware.links` has no config classes of its own any more, the
    real link codes (`visa`, `modbus_tcp`, ...) live in extensions/*.
    """

    def build(self) -> object:
        return object()


class Channel(Device):
    """A live setting whose build value is a config field: what `pwm_channel` is."""

    frequency_hz = Setting("frequency_hz", "Carrier frequency", Quantity("frequency", Hertz))

    def __init__(self, name: str, frequency_hz: float, label: str | None = None) -> None:
        super().__init__(name, label)
        self.frequency_hz.push(frequency_hz)

    @command
    def set_frequency(self, frequency_hz: float) -> None:
        """Change the carrier."""
        self.frequency_hz.push(frequency_hz)


class ChannelConfig(DriverConfig[Channel], type="test_channel"):
    frequency_hz: float = 1000.0

    def build(self, name: str, label: str | None = None) -> Channel:
        return Channel(name, self.frequency_hz, label)


PLANT = {"name": "t1", "type": "sim_plant", "model": "lag", "tau_s": 1.0, "gain": 1.0}
DAQ = {
    "name": "probe",
    "driver": "sim_daq",
    "link": "t1",
    "ports": {"signal": {"port": "output", "quantity": "x", "unit": "1"}},
    "poll_s": 0.1,
}
DRIVE = {"name": "drive", "driver": "sim_drive", "link": "t1", "ports": {"u": "input"}}
CONTROLLER = {"drive.u": {"measured": "probe.signal", "law": {"type": "P", "kp": 0.5}}}


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
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def client(rig, store, runner):
    keep_versions(rig, store, "started bare")
    set_rig(rig)
    set_store(store)
    set_runner(runner)
    with TestClient(create_app()) as c:
        yield c
    set_runner(None)
    set_store(None)
    set_rig(None)
    current = current_rig()
    if current is not None:
        current.close()
    rig.close()


def restart(store: SqliteStore, runner: FakeRunner, rig_file=None) -> Rig:
    """What the runner does after an edit, in-process: close the rig, build the head again.

    A bare runner resumes from the store's head (`--resume`); one from a file loads the file
    and the overlay beside it. The new rig is served; the runner is back.
    """
    old = current_rig()
    if old is not None:
        old.close()
    config = resumed(store.path) if rig_file is None else load_rig_config(rig_file)
    new = config.build(clock=SteppedClock(0))
    edit = runner.edits[-1][0] if runner.edits else None  # FLYBALL_EDIT, as the runner reads it
    keep_versions(new, store, "resumed" if rig_file is None else "loaded", edit)
    set_rig(new)
    runner.restarting = False
    return new


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
        follower = rig.add_entry("follower", device_entry(DRIVE, inputs={"x": "probe.signal"}))
        assert follower.bound["x"].address == "probe.signal"
        rig.remove_device("probe")
        assert follower.bound == {}


def head(store: SqliteStore) -> int:
    row = store.head_rig_version()
    assert row is not None
    return row.id


class TestEdits:
    def test_an_edit_saves_a_version_stops_and_restarts_from_it(
        self, client: TestClient, rig: Rig, store: SqliteStore, runner: FakeRunner
    ) -> None:
        started = head(store)
        r = client.post("/api/links", json=PLANT)
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["previous"] == started and body["version"] == head(store) != started
        assert body["reason"] == "edited: added link t1" and body["restarting"] is True
        assert body["saved"] is None, "a bare rig's edit lives in the store alone"
        assert body["stop"]["reason"].startswith("rig edit: edited: added link t1")
        assert runner.edits == [(body["version"], started, False)]
        assert rig.links == {}, "nothing is applied in place"
        assert store.rig_version(body["version"]).document["links"].keys() == {"t1"}
        # The old process is on its way out: it takes no second edit.
        again = client.post("/api/devices", json=DAQ)
        assert again.status_code == 409 and "restarting" in again.json()["detail"]
        rig = restart(store, runner)
        assert set(rig.links) == {"t1"} and head(store) == body["version"], "nothing new"
        assert client.post("/api/devices", json=DAQ).status_code == 202
        rig = restart(store, runner)
        assert client.post("/api/devices", json=DRIVE).status_code == 202
        rig = restart(store, runner)
        assert set(rig.devices) == {"probe", "drive"} and "probe" in rig.polling.by_name
        r = client.post(
            "/api/controllers",
            json={"output": "drive.u", "measured": "probe.signal", "law": {"type": "P", "kp": 0.5}},
        )
        assert r.status_code == 201, "a controller is still attached in place"
        assert client.delete("/api/links/t1").status_code == 409, "used by probe, drive"
        assert client.delete("/api/devices/nope").status_code == 404
        r = client.delete("/api/devices/probe")
        assert r.status_code == 202, r.text
        document = store.rig_version(r.json()["version"]).document
        assert set(document["devices"]) == {"drive"}
        assert document["controllers"] == {}, "the controller on it goes with it"
        rig = restart(store, runner)
        assert set(rig.devices) == {"drive"} and list(rig.controllers) == []
        reasons = [v["reason"] for v in client.get("/api/rig/versions").json()]
        assert reasons == [
            "edited: removed device probe",
            "attached controller drive.u",
            "edited: added device drive",
            "edited: added device probe",
            "edited: added link t1",
            "started bare",
        ]

    def test_a_refused_edit_changes_nothing(
        self, client: TestClient, rig: Rig, store: SqliteStore, runner: FakeRunner
    ) -> None:
        before = head(store)
        refused = {
            "unknown driver": (client.post, "/api/devices", {"name": "x", "driver": "nope"}, 422),
            "unknown link": (client.post, "/api/devices", {**DAQ, "link": "nope"}, 404),
            "bad link config": (client.post, "/api/links", {"name": "x", "type": "nope"}, 422),
            "no such link": (client.delete, "/api/links/t9", None, 404),
            "no such version": (client.post, "/api/rig/versions/999/restore", None, 404),
            "an input on nothing": (
                client.post,
                "/api/devices",
                {**DRIVE, "name": "d2", "inputs": {"x": "ghost.signal"}},
                404,
            ),
            "a stale base": (client.post, f"/api/links?base={before + 1}", PLANT, 409),
            "a controller on nothing": (
                client.post,
                "/api/rig",
                {"controllers": {"a.b": {"measured": "c.d"}}},
                404,
            ),
        }
        for why, (call, path, body, status) in refused.items():
            r = call(path, json=body) if body is not None else call(path)
            assert r.status_code == status, (why, r.text)
        assert head(store) == before and runner.edits == [] and rig.links == {}
        assert [v["reason"] for v in client.get("/api/rig/versions").json()] == ["started bare"]
        # Unchanged, it is not an edit: nothing is stopped.
        r = client.post(f"/api/rig/versions/{before}/restore")
        assert r.status_code == 409 and "Nothing to change" in r.json()["detail"]
        assert runner.edits == []

    def test_a_running_program_refuses_an_edit_unless_forced(
        self, client: TestClient, store: SqliteStore, runner: FakeRunner
    ) -> None:
        class Running:
            state = type("S", (), {"running": True})()

            def interrupt(self, reason: str) -> bool:
                return True

        set_programmer(Running())  # type: ignore[arg-type]
        try:
            r = client.post("/api/links", json=PLANT)
            assert r.status_code == 409 and "force=true" in r.json()["detail"]
            assert runner.edits == []
            r = client.post("/api/links?force=true", json=PLANT)
            assert r.status_code == 202 and r.json()["stop"]["program_interrupted"] is True
        finally:
            set_programmer(None)

    def test_a_restart_after_an_edit_comes_up_passive(
        self, client: TestClient, store: SqliteStore, runner: FakeRunner
    ) -> None:
        document = {
            "links": {"t1": entry(PLANT)},
            "devices": {"probe": entry(DAQ), "drive": entry(DRIVE)},
            "controllers": CONTROLLER,
        }
        assert client.post("/api/rig", json=document).status_code == 202
        rig = restart(store, runner)
        controller = rig.controllers.resolve("drive.u")
        assert client.post("/api/controllers/drive.u/regulate", json={"at": 5.0}).status_code == 200
        assert controller.mode is ControllerMode.REGULATING
        r = client.post("/api/devices", json={**DAQ, "name": "probe2"})
        assert r.status_code == 202, r.text
        assert r.json()["stop"]["controllers_manual"] == ["drive.u"], "stopped before the restart"
        rig = restart(store, runner)
        assert set(rig.devices) == {"probe", "drive", "probe2"}
        assert rig.controllers.resolve("drive.u").mode is ControllerMode.MANUAL
        assert rig.samples.get("drive.u") is None, "nothing written: the driver's build values"
        assert rig.write_states.get("drive.u") is None

    def test_restore_is_a_new_version_built_whole(
        self, client: TestClient, store: SqliteStore, runner: FakeRunner
    ) -> None:
        first = head(store)
        document = {
            "links": {"t1": entry(PLANT)},
            "devices": {"probe": entry(DAQ), "drive": entry(DRIVE)},
            "controllers": CONTROLLER,
        }
        full = client.post("/api/rig", json=document).json()["version"]
        rig = restart(store, runner)
        assert set(rig.devices) == {"probe", "drive"} and "drive.u" in rig.controllers
        r = client.post(f"/api/rig/versions/{first}/restore")
        assert r.status_code == 202 and r.json()["reason"] == f"restored from {first}"
        rig = restart(store, runner)
        assert rig.devices == {} and rig.links == {} and list(rig.controllers) == []
        versions = client.get("/api/rig/versions").json()
        assert [v["id"] for v in versions if v["head"]] == [r.json()["version"]]
        assert versions[0]["parent"] == full, "a restore is a version on top, not a jump back"
        r = client.post(f"/api/rig/versions/{full}/restore")
        assert r.status_code == 202
        rig = restart(store, runner)
        saved = store.rig_version(full).document
        assert {k: v for k, v in rig.document().items() if k != "controllers"} == {
            k: v for k, v in saved.items() if k != "controllers"
        }
        assert rig.document()["controllers"].keys() == saved["controllers"].keys() == {"drive.u"}

    def test_a_live_setting_is_not_kept_by_an_edit_nor_silently_reverted(
        self, client: TestClient, store: SqliteStore, runner: FakeRunner, _catalog
    ) -> None:
        # The bug D-051 removes: after `set_frequency`, `document()` still rendered the file's
        # value, so a restore in place left the device running at one frequency while the
        # rig said another, and a save wrote the other. Now an edit rebuilds the whole rig:
        # the setting is its build value, and the document says so.
        _catalog.register_device(ChannelConfig)
        channel = {"name": "pwm", "driver": "test_channel", "frequency_hz": 1000.0}
        assert client.post("/api/devices", json=channel).status_code == 202
        rig = restart(store, runner)
        assert (
            client.post(
                "/api/devices/pwm/commands/set_frequency", json={"frequency_hz": 500.0}
            ).status_code
            == 200
        )
        assert rig.devices["pwm"].frequency_hz.value == 500.0  # type: ignore[attr-defined]
        r = client.post("/api/links", json=PLANT)
        assert r.status_code == 202
        saved = store.rig_version(r.json()["version"]).document["devices"]["pwm"]
        assert saved["frequency_hz"] == 1000.0, "the version holds the build value"
        assert rig.devices["pwm"].frequency_hz.value == 500.0, "not touched in place"  # type: ignore[attr-defined]
        rig = restart(store, runner)
        built = rig.devices["pwm"].frequency_hz.value  # type: ignore[attr-defined]
        assert built == 1000.0 == rig.document()["devices"]["pwm"]["frequency_hz"]


class TestSavedOverlay:
    """A rig from files: an edit is saved to the overlay beside the first file."""

    def test_an_edit_writes_the_overlay_the_next_start_loads(
        self, client: TestClient, store: SqliteStore, tmp_path
    ) -> None:
        rig_file = tmp_path / "lab.yaml"
        rig_file.write_text("name: lab\nlinks:\n  t1: {type: sim_plant, model: lag, tau_s: 1.0}\n")
        runner = FakeRunner(origin=Origin((rig_file,)))
        set_runner(runner)
        rig = restart(store, runner, rig_file)
        overlay = tmp_path / "lab.yaml.d" / "added.yaml"
        r = client.post("/api/devices", json=DAQ)
        assert r.status_code == 202, r.text
        assert r.json()["saved"] == str(overlay)
        assert load_document(overlay) == {"devices": {"probe": entry(DAQ)}}
        assert not overlay.with_name("added.yaml.prev").exists(), "there was none before"
        rig = restart(store, runner, rig_file)
        assert set(rig.devices) == {"probe"}
        assert rig.document() == store.rig_version(r.json()["version"]).document
        assert head(store) == r.json()["version"], "the start is at the head: no new row"
        # A second edit keeps the first, and the one it replaced as `.prev`.
        r = client.post("/api/devices", json={**DRIVE})
        assert r.status_code == 202
        assert set(load_document(overlay)["devices"]) == {"probe", "drive"}
        previous = yaml.safe_load(overlay.with_name("added.yaml.prev").read_text())
        assert set(previous["devices"]) == {"probe"}
        rig = restart(store, runner, rig_file)
        assert set(rig.devices) == {"probe", "drive"}
        # The rig file is still the starting document: an edit to it is taken where the
        # overlay does not say otherwise.
        rig_file.write_text("name: lab\nlinks:\n  t1: {type: sim_plant, model: lag, tau_s: 2.0}\n")
        rig = restart(store, runner, rig_file)
        assert rig.document()["links"]["t1"]["tau_s"] == 2.0
        # Removing what the file itself declares is written as a deletion.
        r = client.delete("/api/devices/probe")
        assert r.status_code == 202
        rig = restart(store, runner, rig_file)
        assert client.delete("/api/devices/drive").status_code == 202
        rig = restart(store, runner, rig_file)
        r = client.delete("/api/links/t1")
        assert r.status_code == 202
        assert load_document(overlay) == {"links": {"t1": None}}
        rig = restart(store, runner, rig_file)
        assert rig.links == {} and rig.devices == {}

    def test_a_key_a_set_pins_is_refused(
        self, client: TestClient, store: SqliteStore, tmp_path
    ) -> None:
        rig_file = tmp_path / "lab.yaml"
        rig_file.write_text("name: lab\nlinks:\n  t1: {type: sim_plant, model: lag, tau_s: 1.0}\n")
        sets = ("links.t1.tau_s=3.0",)
        runner = FakeRunner(origin=Origin((rig_file,), sets))
        set_runner(runner)
        old = current_rig()
        if old is not None:
            old.close()
        rig = load_rig_config(rig_file, sets).build(clock=SteppedClock(0))
        set_rig(rig)
        r = client.delete("/api/links/t1")
        assert r.status_code == 409 and "--set" in r.json()["detail"]
        assert not (tmp_path / "lab.yaml.d").exists() and runner.edits == []
        assert client.post("/api/devices", json=DAQ).status_code == 202, "other keys are free"

    def test_the_overlay_and_the_version_agree_or_nothing_is_written(self, tmp_path) -> None:
        rig_file = tmp_path / "lab.yaml"
        rig_file.write_text("name: lab\n")
        later = tmp_path / "lab.yaml.d" / "zz-local.yaml"
        later.parent.mkdir()
        later.write_text("links:\n  t1: {type: sim_plant, model: lag, tau_s: 5.0}\n")
        origin = Origin((rig_file,))
        document = load_rig_config(rig_file).build(start=False).document()
        assert document["links"]["t1"]["tau_s"] == 5.0
        document["links"]["t1"]["tau_s"] = 1.0  # a later overlay would set it back
        with pytest.raises(edits.NotSaveable, match="t1"):
            edits.plan(origin, document)
        document["links"]["t1"]["tau_s"] = 5.0
        document["devices"]["probe"] = entry(DAQ)
        plan = edits.plan(origin, document)
        assert plan.overlay == {"devices": {"probe": entry(DAQ)}}
        edits.write(plan)
        rebuilt = load_rig_config(rig_file).build(start=False)
        assert rebuilt.document() == plan.document
        rebuilt.close()


class TestSave:
    def test_save_writes_an_overlay_by_default_and_a_whole_rig_to_a_path(
        self, client: TestClient, rig: Rig, tmp_path
    ) -> None:
        assert client.post("/api/rig/save").status_code == 409, "not started from a file: say where"
        rig_file = tmp_path / "lab.yaml"
        rig_file.write_text("name: lab\n")
        rig.files = [rig_file]
        rig.add_link("t1", link_config(PLANT))  # changed since the start (as a controller is)
        rig.add_entry("probe", device_entry(DAQ))
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

    def test_a_later_run_s_save_keeps_what_an_earlier_run_saved(
        self, client: TestClient, store: SqliteStore, tmp_path
    ) -> None:
        from flyball.runtime.config import load_rig_config

        rig_file = tmp_path / "lab.yaml"
        rig_file.write_text("name: lab\n")
        overlay = tmp_path / "lab.yaml.d" / "added.yaml"

        def run() -> Rig:  # a start from the file, as the runner does: the saved overlay comes too
            rig = load_rig_config(rig_file).build(clock=SteppedClock(0), start=False)
            set_rig(rig)
            return rig

        first = run()
        assert first.saved_overlay == {}
        first.add_link("t1", link_config(PLANT))
        assert client.post("/api/rig/save").json()["written"] is True
        second = run()
        assert "t1" in second.links and second.saved_overlay["links"].keys() == {"t1"}
        r = client.post("/api/rig/save")
        assert r.status_code == 200 and r.json()["written"] is False, "nothing new: file untouched"
        second.add_entry("probe", device_entry(DAQ))
        assert client.post("/api/rig/save").json()["written"] is True
        saved = load_document(overlay)
        assert set(saved["links"]) == {"t1"}, "the first run's link survives the second's save"
        assert set(saved["devices"]) == {"probe"}
        third = run()
        assert set(third.links) == {"t1"} and set(third.devices) == {"probe"}
        # A removal is saved too, and the next run starts without it.
        third.remove_device("probe")
        assert client.post("/api/rig/save").json()["written"] is True
        assert set(run().devices) == set()
        for rig in (first, second, third):
            rig.close()

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
        rig.add_link("t1", link_config(PLANT))
        overlay = tmp_path / "lab.yaml.d" / "added.yaml"
        overlay.parent.mkdir()
        overlay.write_text("devices: {}\n")
        r = client.post("/api/rig/save", json={"path": str(rig_file), "overwrite": True})
        assert r.status_code == 200, r.text
        assert "runner" not in r.json()["document"], "the secrets are not sent back"
        saved = load_document(rig_file)
        assert saved["runner"] == {
            "auth": {"token": "base-token", "password": "hunter2"},
            "allow_save": True,
        }, "the file's own runner section, with what it extended, is kept"
        assert "t1" in saved["links"]
        assert not overlay.exists(), "flattened into the file: the overlay is cleared"
        assert overlay.with_name("added.yaml.prev").read_text() == "devices: {}\n"
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
            assert client.post("/api/links", json=PLANT).status_code == 202
        finally:
            set_compose(False)


def test_a_batch_read_gives_null_for_a_signal_never_read(client: TestClient, rig: Rig) -> None:
    rig.add_link("t1", link_config(PLANT))
    rig.add_entry("drive", device_entry(DRIVE))
    rig.add_entry("probe", device_entry(DAQ))
    assert client.put("/api/signals/drive.u", json=3.0).status_code == 200
    got = client.get("/api/read", params={"at": "drive.u,probe.signal"}).json()
    assert got[0] is not None and got[1] is None
