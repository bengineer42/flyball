"""The runner builds a rig from a file, records if told to, and serves it with a programmer."""

from __future__ import annotations

from pathlib import Path

import pytest

from flyball import runner
from flyball.runtime.config import RunnerConfig, load_rig_config

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"


@pytest.fixture
def oven():
    return load_rig_config(EXAMPLES / "oven.yaml")


def test_start_builds_the_rig_and_records_only_when_asked(tmp_path, oven):
    rig = runner.start(oven)
    try:
        assert rig.name == "oven" and rig.recorder is None and list(rig.controllers)
    finally:
        rig.polling.stop_all()


def test_start_records_when_asked(tmp_path, oven):
    rig = runner.start(oven, record=True, store_path=tmp_path / "s.sqlite")
    try:
        assert rig.recorder is not None and (tmp_path / "s.sqlite").exists()
    finally:
        rig.stop_recording()
        rig.polling.stop_all()


def test_the_file_s_recording_flag_is_the_default(tmp_path, oven):
    config = oven.model_copy(update={"recording": True})
    rig = runner.start(config, store_path=tmp_path / "s.sqlite")
    try:
        assert rig.recorder is not None
    finally:
        rig.stop_recording()
        rig.polling.stop_all()


def test_an_explicit_no_beats_the_file(tmp_path, oven):
    config = oven.model_copy(update={"recording": True})
    rig = runner.start(config, record=False, store_path=tmp_path / "t.sqlite")
    try:
        assert rig.recorder is None
    finally:
        rig.polling.stop_all()


def test_a_bad_file_is_a_message_not_a_traceback(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nlinks:\n  p: { tag: nope }\n")
    assert runner.main([str(bad)]) == 2
    assert "nope" in capsys.readouterr().err


def test_serve_attaches_rig_and_programmer_and_detaches_after(monkeypatch):
    from flyball.interfaces.server import deps
    from flyball.rig import Rig

    seen = {}

    def fake_run(self):
        seen["rig"] = deps.current_rig()
        seen["programmer"] = deps.get_programmer()

    monkeypatch.setattr("uvicorn.Server.run", fake_run)
    rig = Rig("t")
    runner.serve(rig, RunnerConfig(port=1, log_level="warning"))
    assert seen["rig"] is rig and seen["programmer"].rig is rig
    assert deps.current_rig() is None
    with pytest.raises(Exception, match="No programmer"):
        deps.get_programmer()


def _routes_served(monkeypatch, **settings):
    from flyball.rig import Rig

    seen = {}
    monkeypatch.setattr("uvicorn.Server.run", lambda self: seen.setdefault("app", self.config.app))
    runner.serve(Rig("t"), RunnerConfig(port=1, log_level="warning", **settings))
    return {getattr(r, "path", "") for r in seen["app"].routes}


def test_mcp_is_mounted_unless_switched_off(monkeypatch):
    assert any(p.startswith("/mcp") for p in _routes_served(monkeypatch))
    assert not any(p.startswith("/mcp") for p in _routes_served(monkeypatch, mcp=False))


def test_no_mcp_flag_and_env(monkeypatch):
    monkeypatch.delenv("FLYBALL_NO_MCP", raising=False)
    assert runner.parser().parse_args([]).mcp is None, "unset: the file's value stands"
    assert runner.parser().parse_args(["--no-mcp"]).mcp is False
    monkeypatch.setenv("FLYBALL_NO_MCP", "1")
    assert runner.parser().parse_args([]).mcp is False


class TestSettle:
    """The `runner:` section under the command line."""

    def parse(self, *argv: str):
        return runner.parser().parse_args(list(argv))

    def test_defaults_sit_beside_the_first_rig_file(self, tmp_path):
        s = runner.settle(None, self.parse(), tmp_path / "lab.yaml")
        assert (s.host, s.port, s.mcp, s.allow_save, s.allow_shutdown) == (
            "127.0.0.1",
            8000,
            True,
            False,
            False,
        )
        assert s.store == tmp_path / "lab.sqlite" and s.programs == tmp_path / "programs"
        assert s.drivers == tmp_path / "drivers" and s.tunings == tmp_path / "tunings"

    def test_a_deployment_wrapper_finds_the_extended_base_file_s_programs(self, tmp_path):
        """A wrapper `extends`ing a base rig.

        Still finds the base's `programs/`/`tunings`/`drivers`, not just what's beside the
        wrapper itself -- the bug confirmed 18 Sep (six Docker sims silently found no
        libraries at all).
        """
        base_dir = tmp_path / "base"
        wrapper_dir = tmp_path / "site"
        (base_dir / "programs").mkdir(parents=True)
        (base_dir / "tunings").mkdir(parents=True)
        wrapper_dir.mkdir()
        first = wrapper_dir / "wrapper.yaml"
        s = runner.settle(None, self.parse(), first, layers=[base_dir / "rig.yaml"])
        assert s.programs == base_dir / "programs"
        assert s.tunings == base_dir / "tunings"
        # drivers/ exists beside neither -- falls back to the first file, unchanged behaviour
        assert s.drivers == wrapper_dir / "drivers"

    def test_the_wrapper_s_own_directory_still_wins_when_it_has_one_too(self, tmp_path):
        base_dir = tmp_path / "base"
        wrapper_dir = tmp_path / "site"
        (base_dir / "programs").mkdir(parents=True)
        (wrapper_dir / "programs").mkdir(parents=True)
        first = wrapper_dir / "wrapper.yaml"
        s = runner.settle(None, self.parse(), first, layers=[base_dir / "rig.yaml"])
        assert s.programs == wrapper_dir / "programs"

    def test_no_layers_given_behaves_exactly_as_before(self, tmp_path):
        s = runner.settle(None, self.parse(), tmp_path / "lab.yaml", layers=[])
        assert s.programs == tmp_path / "programs"

    def test_the_file_sets_and_the_command_line_overrides(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
        section = RunnerConfig(port=9000, allow_save=True, mcp=False, token="filed")
        s = runner.settle(section, self.parse("--port", "9001", "--no-mcp"), tmp_path / "r.yaml")
        assert (s.port, s.allow_save, s.mcp, s.auth.token) == (9001, True, False, "filed")
        s = runner.settle(section, self.parse("--token", "given"), tmp_path / "r.yaml")
        assert s.auth.token == "given"

    def test_the_auth_section_settles_like_the_rest(self, tmp_path, monkeypatch):
        for var in ("FLYBALL_TOKEN", "FLYBALL_PASSWORD", "FLYBALL_ANONYMOUS", "FLYBALL_SESSION"):
            monkeypatch.delenv(var, raising=False)
        section = RunnerConfig(auth={"password": "filed", "anonymous": "read", "session": "1h"})
        s = runner.settle(section, self.parse(), tmp_path / "r.yaml")
        assert (s.auth.password, s.auth.anonymous, s.auth.session_s) == ("filed", "read", 3600)
        args = self.parse("--password", "given", "--anonymous", "none", "--session", "30m")
        s = runner.settle(section, args, tmp_path / "r.yaml")
        assert (s.auth.password, s.auth.anonymous, s.auth.session_s) == ("given", "none", 1800)
        assert s.auth.token is None and not RunnerConfig().auth.enabled
        monkeypatch.setenv("FLYBALL_PASSWORD", "env")
        assert runner.settle(None, self.parse(), tmp_path / "r.yaml").auth.password == "env"

    def test_a_path_in_the_file_is_relative_to_the_rig_and_the_flags_to_the_cwd(self, tmp_path):
        section = RunnerConfig(store=Path("data/x.sqlite"), drivers=Path("/abs/drivers"))
        s = runner.settle(section, self.parse("--programs", "p"), tmp_path / "r.yaml")
        assert s.store == tmp_path / "data/x.sqlite" and s.drivers == Path("/abs/drivers")
        assert s.programs == Path("p")

    def test_store_dir_names_the_store_after_the_rig(self, tmp_path):
        section = RunnerConfig(store_dir=Path("stores"))
        s = runner.settle(section, self.parse(), tmp_path / "r.yaml", name="furnace")
        assert s.store == tmp_path / "stores" / "furnace.sqlite"
        s = runner.settle(section, self.parse(), tmp_path / "r.yaml")
        assert s.store == tmp_path / "stores" / "r.sqlite", "no name: the file's stem"
        s = runner.settle(section, self.parse("--store", "here.sqlite"), tmp_path / "r.yaml")
        assert s.store == Path("here.sqlite"), "--store wins"

    def test_run_is_a_freeform_escape_hatch_never_inspected(self, tmp_path):
        # `runner.run` is Go-CLI-only (`flyball run`'s --serve-ui/--uv); Python must accept
        # any dict here without validating or acting on its contents.
        section = RunnerConfig(run={"anything": "goes", "here": 123})
        assert section.run == {"anything": "goes", "here": 123}
        s = runner.settle(section, self.parse(), tmp_path / "r.yaml")
        assert s.run == {"anything": "goes", "here": 123}
        assert RunnerConfig().run == {}


def test_main_reads_the_runner_section_from_the_rig_file(tmp_path, monkeypatch):
    rig_file = tmp_path / "lab.yaml"
    rig_file.write_text("name: lab\nrunner: {port: 9123, allow_shutdown: true}\n")
    seen = {}
    monkeypatch.setattr(
        "flyball.runner.entrypoint.serve", lambda rig, settings, **kw: seen.update(s=settings)
    )
    assert runner.main([str(rig_file)]) == 0
    assert seen["s"].port == 9123 and seen["s"].allow_shutdown is True
    assert runner.main([str(rig_file), "--port", "9124"]) == 0
    assert seen["s"].port == 9124


def test_a_runner_only_file_extends_the_rig(tmp_path, monkeypatch):
    (tmp_path / "lab.yaml").write_text("name: lab\n")
    site = tmp_path / "site.yaml"
    site.write_text("extends: [lab.yaml]\nrunner: {port: 9125}\n")
    seen = {}
    monkeypatch.setattr(
        "flyball.runner.entrypoint.serve",
        lambda rig, settings, **kw: seen.update(s=settings, rig=rig),
    )
    assert runner.main([str(site)]) == 0
    assert seen["s"].port == 9125 and seen["rig"].name == "lab"


def test_start_with_store_closes_sessions_an_earlier_run_left_open(tmp_path, oven):
    from flyball.record.sqlite import SqliteStore

    path = tmp_path / "s.sqlite"
    store = SqliteStore(path)
    orphan = store.open_session(start_ns=1_000, config=None).session
    store.close()
    rig, store = runner.start_with_store(oven, record=True, store_path=path)
    try:
        sessions = {s.id: s for s in store.sessions()}
        assert sessions[orphan.id].end_ns is not None, "the orphan was closed"
        assert rig.recorder is not None and sum(s.open for s in sessions.values()) == 1
    finally:
        rig.polling.stop_all()
        rig.stop_recording()


def test_start_with_store_finishes_a_delete_an_earlier_run_cut_off(tmp_path, oven):
    from flyball.record.sqlite import SqliteStore

    path = tmp_path / "s.sqlite"
    store = SqliteStore(path)
    half = store.open_session(start_ns=1_000, details={"name": "old"})
    half.end(2_000)
    kept = store.open_session(start_ns=3_000).session
    store.end_session(kept.id, 4_000)
    with store._transaction() as connection:  # as delete_session leaves it, cut off
        connection.execute(
            "UPDATE session SET details = ? WHERE id = ?",
            ('{"name":"old","deleting":true}', half.session.id),
        )
    store.close()
    rig, store = runner.start_with_store(oven, store_path=path)
    try:
        assert store.deleting_sessions() == []
        assert half.session.id not in [s.id for s in store.sessions()]
        assert kept.id in [s.id for s in store.sessions()]
    finally:
        rig.polling.stop_all()


def test_a_restart_asked_over_the_api_execs_the_same_command_line(monkeypatch):
    from flyball.interfaces.server import deps
    from flyball.rig import Rig

    execs = []

    def fake_run(self):
        deps.current_runner().restart()
        assert self.should_exit

    monkeypatch.setattr("uvicorn.Server.run", fake_run)
    monkeypatch.setattr("os.execv", lambda exe, argv: execs.append((exe, argv)))
    monkeypatch.setattr("sys.argv", ["flyball-runner", "rig.yaml", "--port", "1"])
    runner.serve(Rig("t"), RunnerConfig(port=1, log_level="warning"))
    import sys

    assert execs == [
        (sys.executable, [sys.executable, "flyball-runner", "rig.yaml", "--port", "1"])
    ]
    assert deps.current_runner() is None


SIM_LINK = {"tag": "sim_plant", "model": "lag", "tau_s": 1.0, "gain": 1.0}


def test_resume_follows_the_head_back_to_the_last_change(tmp_path):
    from flyball.record.sqlite import SqliteStore

    path = tmp_path / "s.sqlite"
    store = SqliteStore(path)
    v1 = store.save_rig_version(1, "loaded", {"name": "a"})
    v2 = store.save_rig_version(2, "added link x", {"name": "a", "links": {"x": SIM_LINK}})
    v3 = store.save_rig_version(3, "loaded", {"name": "a"})  # a plain restart: parent v2
    assert (v1.parent, v2.parent, v3.parent) == (None, v1.id, v2.id)
    assert runner.resumed(path).name == "a" and runner.resumed(path).links.keys() == {"x"}
    store.set_rig_head(v1.id)  # restored to the first: nothing after it counts
    assert runner.resumed(path).links == {}
    v4 = store.save_rig_version(4, "added link y", {"name": "a", "links": {"y": SIM_LINK}})
    assert v4.parent == v1.id and store.head_rig_version().id == v4.id
    store.close()


def test_a_start_at_the_head_records_nothing(tmp_path, oven):

    rig, store = runner.start_with_store(oven, store_path=tmp_path / "s.sqlite")
    try:
        first = store.head_rig_version()
        assert first is not None and first.reason == "loaded"
        runner.keep_versions(rig, store, "loaded")
        assert store.head_rig_version().id == first.id
    finally:
        rig.polling.stop_all()
        store.close()


def test_the_migration_chains_versions_already_stored(tmp_path):
    import sqlite3

    from flyball.record.sqlite import SqliteStore

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


class TestExposure:
    """An open runner -- no password, no token -- is served on loopback only, unless asked.

    A misconfiguration removes exposure, never operation: the runner still starts.
    """

    @pytest.fixture(autouse=True)
    def _no_env(self, monkeypatch):
        for name in ("FLYBALL_PASSWORD", "FLYBALL_TOKEN", "FLYBALL_INSECURE_OPEN"):
            monkeypatch.delenv(name, raising=False)

    @pytest.fixture
    def served(self, monkeypatch):
        seen: dict = {}
        monkeypatch.setattr(
            "flyball.runner.entrypoint.serve",
            lambda rig, settings, **kw: seen.update(s=settings, kw=kw),
        )
        return seen

    @pytest.fixture
    def lab(self, tmp_path):
        rig_file = tmp_path / "lab.yaml"
        rig_file.write_text("name: lab\n")
        return rig_file

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.1.1"])
    def test_loopback_stays_open(self, lab, served, host):
        assert runner.main([str(lab), "--host", host]) == 0
        assert not served["s"].auth.enabled

    @pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.3", "", "pi.local"])
    def test_an_open_runner_beyond_loopback_still_starts(self, lab, served, host):
        assert runner.main([str(lab), "--host", host]) == 0, "the rig runs; only exposure goes"
        assert served["kw"]["insecure_open"] is False

    @pytest.mark.parametrize("value", ["1", "true", "yes"])
    def test_the_opt_in_is_a_flag_or_the_environment(self, lab, served, monkeypatch, value):
        assert runner.main([str(lab), "--host", "0.0.0.0", "--insecure-open"]) == 0
        assert served["kw"]["insecure_open"] is True
        monkeypatch.setenv("FLYBALL_INSECURE_OPEN", value)
        assert runner.main([str(lab), "--host", "0.0.0.0"]) == 0
        assert served["kw"]["insecure_open"] is True

    def test_the_opt_in_is_never_a_rig_file_key(self, tmp_path, served, capsys):
        # A file can be pasted from a forum or pulled in by `extends:`; the switch is per run.
        rig_file = tmp_path / "opt.yaml"
        rig_file.write_text("name: lab\nrunner: {host: 0.0.0.0, auth: {insecure_open: true}}\n")
        assert runner.main([str(rig_file)]) == 2
        assert "insecure_open" in capsys.readouterr().err
        assert "s" not in served
        from flyball.runtime.config import AuthConfig

        assert "insecure_open" not in AuthConfig.model_json_schema()["properties"]

    @pytest.mark.parametrize(
        "argv, env",
        [
            (["--password", "hunter2"], {}),
            (["--token", "t0k"], {}),
            ([], {"FLYBALL_PASSWORD": "hunter2"}),
            ([], {"FLYBALL_TOKEN": "t0k"}),
        ],
    )
    def test_a_password_or_token_lets_it_bind_anywhere(self, lab, served, monkeypatch, argv, env):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        assert runner.main([str(lab), "--host", "0.0.0.0", *argv]) == 0
        assert served["s"].auth.enabled

    def test_the_decision(self):
        from flyball.runtime.config import AuthConfig, settle_exposure

        loopback = settle_exposure(RunnerConfig())
        assert (loopback.host, loopback.restricted, loopback.warning) == ("127.0.0.1", False, None)
        moved = settle_exposure(RunnerConfig(host="0.0.0.0", port=8123))
        assert moved.host == "127.0.0.1" and moved.requested == "0.0.0.0" and moved.restricted
        assert not moved.open_network and moved.open
        assert moved.warning is not None
        for needed in ("127.0.0.1:8123", "password", "token", "--insecure-open"):
            assert needed in moved.warning, needed
        assert "FLYBALL_INSECURE_OPEN" in moved.warning
        assert settle_exposure(RunnerConfig(host="::")).host == "127.0.0.1"
        opened = settle_exposure(RunnerConfig(host="0.0.0.0"), insecure_open=True)
        assert opened.host == "0.0.0.0" and opened.open_network and not opened.restricted
        assert opened.warning is not None and "anyone" in opened.warning
        clear = settle_exposure(RunnerConfig(host="0.0.0.0", auth=AuthConfig(password="x")))
        assert clear.host == "0.0.0.0" and not clear.open and not clear.open_network
        assert clear.warning is not None and "unencrypted" in clear.warning
        assert settle_exposure(RunnerConfig(auth=AuthConfig(token="x"))).warning is None

    def test_serve_binds_loopback_and_says_why_once(self, monkeypatch, capsys):
        from flyball.rig import Rig

        bound: list = []
        monkeypatch.setattr("uvicorn.Server.run", lambda self: bound.append(self.config.host))
        runner.serve(Rig("t"), RunnerConfig(host="0.0.0.0", port=1))
        assert bound == ["127.0.0.1"]
        err = capsys.readouterr().err
        assert err.count("WARNING") == 1 and "127.0.0.1:1" in err

    def test_serve_binds_where_asked_when_opted_in(self, monkeypatch, capsys):
        from flyball.rig import Rig

        bound: list = []
        monkeypatch.setattr("uvicorn.Server.run", lambda self: bound.append(self.config.host))
        runner.serve(Rig("t"), RunnerConfig(host="0.0.0.0", port=1), insecure_open=True)
        assert bound == ["0.0.0.0"]
        assert capsys.readouterr().err.count("OPEN") == 1

    def test_serve_warns_of_cleartext_once(self, monkeypatch, caplog):
        from flyball.rig import Rig
        from flyball.runtime.config import AuthConfig

        monkeypatch.setattr("uvicorn.Server.run", lambda self: None)
        with caplog.at_level("WARNING", logger="flyball.runner"):
            runner.serve(Rig("t"), RunnerConfig(host="0.0.0.0", port=1, auth=AuthConfig(token="x")))
        warnings = [r for r in caplog.records if "unencrypted" in r.getMessage()]
        assert len(warnings) == 1
