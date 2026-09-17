"""The daemon builds a rig from a file, records if told to, and serves it with a programmer."""

from __future__ import annotations

from pathlib import Path

import pytest

from flyball import daemon
from flyball.runtime.config import DaemonConfig, load_rig_config

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"


@pytest.fixture
def oven():
    return load_rig_config(EXAMPLES / "oven.yaml")


def test_start_builds_the_rig_and_records_only_when_asked(tmp_path, oven):
    rig = daemon.start(oven)
    try:
        assert rig.name == "oven" and rig.recorder is None and list(rig.controllers)
    finally:
        rig.polling.stop_all()


def test_start_records_when_asked(tmp_path, oven):
    rig = daemon.start(oven, record=True, store_path=tmp_path / "s.sqlite")
    try:
        assert rig.recorder is not None and (tmp_path / "s.sqlite").exists()
    finally:
        rig.stop_recording()
        rig.polling.stop_all()


def test_the_file_s_recording_flag_is_the_default(tmp_path, oven):
    config = oven.model_copy(update={"recording": True})
    rig = daemon.start(config, store_path=tmp_path / "s.sqlite")
    try:
        assert rig.recorder is not None
    finally:
        rig.stop_recording()
        rig.polling.stop_all()


def test_an_explicit_no_beats_the_file(tmp_path, oven):
    config = oven.model_copy(update={"recording": True})
    rig = daemon.start(config, record=False, store_path=tmp_path / "t.sqlite")
    try:
        assert rig.recorder is None
    finally:
        rig.polling.stop_all()


def test_a_bad_file_is_a_message_not_a_traceback(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nlinks:\n  p: { tag: nope }\n")
    assert daemon.main([str(bad)]) == 2
    assert "nope" in capsys.readouterr().err


def test_serve_attaches_rig_and_programmer_and_detaches_after(monkeypatch):
    from flyball.runtime.rig import Rig
    from flyball.server import deps

    seen = {}

    def fake_run(self):
        seen["rig"] = deps.current_rig()
        seen["programmer"] = deps.get_programmer()

    monkeypatch.setattr("uvicorn.Server.run", fake_run)
    rig = Rig("t")
    daemon.serve(rig, DaemonConfig(port=1, log_level="warning"))
    assert seen["rig"] is rig and seen["programmer"].rig is rig
    assert deps.current_rig() is None
    with pytest.raises(Exception, match="No programmer"):
        deps.get_programmer()


def _routes_served(monkeypatch, **settings):
    from flyball.runtime.rig import Rig

    seen = {}
    monkeypatch.setattr("uvicorn.Server.run", lambda self: seen.setdefault("app", self.config.app))
    daemon.serve(Rig("t"), DaemonConfig(port=1, log_level="warning", **settings))
    return {getattr(r, "path", "") for r in seen["app"].routes}


def test_mcp_is_mounted_unless_switched_off(monkeypatch):
    assert any(p.startswith("/mcp") for p in _routes_served(monkeypatch))
    assert not any(p.startswith("/mcp") for p in _routes_served(monkeypatch, mcp=False))


def test_no_mcp_flag_and_env(monkeypatch):
    monkeypatch.delenv("FLYBALL_NO_MCP", raising=False)
    assert daemon.parser().parse_args([]).mcp is None, "unset: the file's value stands"
    assert daemon.parser().parse_args(["--no-mcp"]).mcp is False
    monkeypatch.setenv("FLYBALL_NO_MCP", "1")
    assert daemon.parser().parse_args([]).mcp is False


class TestSettle:
    """The `daemon:` section under the command line."""

    def parse(self, *argv: str):
        return daemon.parser().parse_args(list(argv))

    def test_defaults_sit_beside_the_first_rig_file(self, tmp_path):
        s = daemon.settle(None, self.parse(), tmp_path / "lab.yaml")
        assert (s.host, s.port, s.mcp, s.allow_save, s.allow_shutdown) == (
            "127.0.0.1",
            8000,
            True,
            False,
            False,
        )
        assert s.store == tmp_path / "lab.sqlite" and s.programs == tmp_path / "programs"
        assert s.drivers == tmp_path / "drivers" and s.tunings == tmp_path / "tunings"

    def test_the_file_sets_and_the_command_line_overrides(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
        section = DaemonConfig(port=9000, allow_save=True, mcp=False, token="filed")
        s = daemon.settle(section, self.parse("--port", "9001", "--no-mcp"), tmp_path / "r.yaml")
        assert (s.port, s.allow_save, s.mcp, s.auth.token) == (9001, True, False, "filed")
        s = daemon.settle(section, self.parse("--token", "given"), tmp_path / "r.yaml")
        assert s.auth.token == "given"

    def test_the_auth_section_settles_like_the_rest(self, tmp_path, monkeypatch):
        for var in ("FLYBALL_TOKEN", "FLYBALL_PASSWORD", "FLYBALL_ANONYMOUS", "FLYBALL_SESSION"):
            monkeypatch.delenv(var, raising=False)
        section = DaemonConfig(auth={"password": "filed", "anonymous": "read", "session": "1h"})
        s = daemon.settle(section, self.parse(), tmp_path / "r.yaml")
        assert (s.auth.password, s.auth.anonymous, s.auth.session_s) == ("filed", "read", 3600)
        args = self.parse("--password", "given", "--anonymous", "none", "--session", "30m")
        s = daemon.settle(section, args, tmp_path / "r.yaml")
        assert (s.auth.password, s.auth.anonymous, s.auth.session_s) == ("given", "none", 1800)
        assert s.auth.token is None and not DaemonConfig().auth.enabled
        monkeypatch.setenv("FLYBALL_PASSWORD", "env")
        assert daemon.settle(None, self.parse(), tmp_path / "r.yaml").auth.password == "env"

    def test_a_path_in_the_file_is_relative_to_the_rig_and_the_flags_to_the_cwd(self, tmp_path):
        section = DaemonConfig(store=Path("data/x.sqlite"), drivers=Path("/abs/drivers"))
        s = daemon.settle(section, self.parse("--programs", "p"), tmp_path / "r.yaml")
        assert s.store == tmp_path / "data/x.sqlite" and s.drivers == Path("/abs/drivers")
        assert s.programs == Path("p")

    def test_store_dir_names_the_store_after_the_rig(self, tmp_path):
        section = DaemonConfig(store_dir=Path("stores"))
        s = daemon.settle(section, self.parse(), tmp_path / "r.yaml", name="furnace")
        assert s.store == tmp_path / "stores" / "furnace.sqlite"
        s = daemon.settle(section, self.parse(), tmp_path / "r.yaml")
        assert s.store == tmp_path / "stores" / "r.sqlite", "no name: the file's stem"
        s = daemon.settle(section, self.parse("--store", "here.sqlite"), tmp_path / "r.yaml")
        assert s.store == Path("here.sqlite"), "--store wins"


def test_main_reads_the_daemon_section_from_the_rig_file(tmp_path, monkeypatch):
    rig_file = tmp_path / "lab.yaml"
    rig_file.write_text("name: lab\ndaemon: {port: 9123, allow_shutdown: true}\n")
    seen = {}
    monkeypatch.setattr("flyball.daemon.serve", lambda rig, settings, **kw: seen.update(s=settings))
    assert daemon.main([str(rig_file)]) == 0
    assert seen["s"].port == 9123 and seen["s"].allow_shutdown is True
    assert daemon.main([str(rig_file), "--port", "9124"]) == 0
    assert seen["s"].port == 9124


def test_a_daemon_only_file_extends_the_rig(tmp_path, monkeypatch):
    (tmp_path / "lab.yaml").write_text("name: lab\n")
    site = tmp_path / "site.yaml"
    site.write_text("extends: [lab.yaml]\ndaemon: {port: 9125}\n")
    seen = {}
    monkeypatch.setattr(
        "flyball.daemon.serve", lambda rig, settings, **kw: seen.update(s=settings, rig=rig)
    )
    assert daemon.main([str(site)]) == 0
    assert seen["s"].port == 9125 and seen["rig"].name == "lab"


def test_start_with_store_closes_sessions_an_earlier_run_left_open(tmp_path, oven):
    from flyball.db.sqlite import SqliteStore

    path = tmp_path / "s.sqlite"
    store = SqliteStore(path)
    orphan = store.open_session(start_ns=1_000, config=None).session
    store.close()
    rig, store = daemon.start_with_store(oven, record=True, store_path=path)
    try:
        sessions = {s.id: s for s in store.sessions()}
        assert sessions[orphan.id].end_ns is not None, "the orphan was closed"
        assert rig.recorder is not None and sum(s.open for s in sessions.values()) == 1
    finally:
        rig.polling.stop_all()
        rig.stop_recording()


def test_a_restart_asked_over_the_api_execs_the_same_command_line(monkeypatch):
    from flyball.runtime.rig import Rig
    from flyball.server import deps

    execs = []

    def fake_run(self):
        deps.current_daemon().restart()
        assert self.should_exit

    monkeypatch.setattr("uvicorn.Server.run", fake_run)
    monkeypatch.setattr("os.execv", lambda exe, argv: execs.append((exe, argv)))
    monkeypatch.setattr("sys.argv", ["flyball-daemon", "rig.yaml", "--port", "1"])
    daemon.serve(Rig("t"), DaemonConfig(port=1, log_level="warning"))
    import sys

    assert execs == [
        (sys.executable, [sys.executable, "flyball-daemon", "rig.yaml", "--port", "1"])
    ]
    assert deps.current_daemon() is None


SIM_LINK = {"tag": "sim_plant", "model": "lag", "tau_s": 1.0, "gain": 1.0}


def test_resume_follows_the_head_back_to_the_last_change(tmp_path):
    from flyball.db.sqlite import SqliteStore

    path = tmp_path / "s.sqlite"
    store = SqliteStore(path)
    v1 = store.save_rig_version(1, "loaded", {"name": "a"})
    v2 = store.save_rig_version(2, "added link x", {"name": "a", "links": {"x": SIM_LINK}})
    v3 = store.save_rig_version(3, "loaded", {"name": "a"})  # a plain restart: parent v2
    assert (v1.parent, v2.parent, v3.parent) == (None, v1.id, v2.id)
    assert daemon.resumed(path).name == "a" and daemon.resumed(path).links.keys() == {"x"}
    store.set_rig_head(v1.id)  # restored to the first: nothing after it counts
    assert daemon.resumed(path).links == {}
    v4 = store.save_rig_version(4, "added link y", {"name": "a", "links": {"y": SIM_LINK}})
    assert v4.parent == v1.id and store.head_rig_version().id == v4.id
    store.close()


def test_a_start_at_the_head_records_nothing(tmp_path, oven):

    rig, store = daemon.start_with_store(oven, store_path=tmp_path / "s.sqlite")
    try:
        first = store.head_rig_version()
        assert first is not None and first.reason == "loaded"
        daemon.keep_versions(rig, store, "loaded")
        assert store.head_rig_version().id == first.id
    finally:
        rig.polling.stop_all()
        store.close()


def test_the_migration_chains_versions_already_stored(tmp_path):
    import sqlite3

    from flyball.db.sqlite import SqliteStore

    path = tmp_path / "old.sqlite"
    store = SqliteStore(path)  # every migration, including 0009, on an empty store
    store.close()
    with sqlite3.connect(path) as db:  # then pretend three rows predate it
        db.execute("DROP INDEX session_by_kind")  # 0010's, or it would run again too
        for column in ("kind", "origin_ns", "pinned", "continues", "bytes"):
            db.execute(f"ALTER TABLE session DROP COLUMN {column}")
        db.execute("DROP TABLE rig_head")
        db.execute("ALTER TABLE rig_version DROP COLUMN parent_id")
        for i in (1, 2, 3):
            db.execute(
                "INSERT INTO rig_version (time_ns, reason, files, document)"
                " VALUES (?, ?, '[]', '{}')",
                (i, f"v{i}"),
            )
        db.execute("UPDATE schema_version SET version = 8")
    store = SqliteStore(path)
    rows = store.rig_versions()
    assert [(r.id, r.parent) for r in rows] == [(3, 2), (2, 1), (1, None)]
    assert store.head_rig_version().id == 3
    store.close()
