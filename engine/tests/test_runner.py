"""The runner builds a rig from a file, records if told to, and serves it with a programmer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from flyball import runner
from flyball.runner.entrypoint import _needed_extras
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
    bad.write_text("name: x\nlinks:\n  p: { type: nope }\n")
    assert runner.main([str(bad)]) == 2
    assert "nope" in capsys.readouterr().err


def test_a_missing_server_extra_is_one_line_not_a_traceback(monkeypatch, capsys, tmp_path):
    monkeypatch.setitem(sys.modules, "fastapi", None)
    with pytest.raises(SystemExit) as excinfo:
        runner.main([str(EXAMPLES / "oven.yaml"), "--store", str(tmp_path / "s.sqlite")])
    assert excinfo.value.code == 2
    assert capsys.readouterr().err == (
        "flyball-runner: needs the server extra -- pip install 'flyball[server]'"
        " (missing: fastapi)\n"
    )


class _Args:
    """A stand-in for the parsed `argparse.Namespace`, just the fields `_needed_extras` reads."""

    def __init__(self, rig: list[str], mcp: bool | None = None) -> None:
        self.rig = [Path(p) for p in rig]
        self.mcp = mcp


def test_needed_extras_always_wants_fastapi_and_uvicorn():
    needed = _needed_extras(_Args(rig=[]))
    assert "fastapi" in needed and "uvicorn" in needed


def test_needed_extras_wants_mcp_and_httpx_unless_no_mcp():
    assert "mcp" in _needed_extras(_Args(rig=[], mcp=None))
    assert "httpx" in _needed_extras(_Args(rig=[], mcp=None))
    assert "mcp" not in _needed_extras(_Args(rig=[], mcp=False))
    assert "httpx" not in _needed_extras(_Args(rig=[], mcp=False))


def test_needed_extras_wants_yaml_only_for_a_yaml_rig_file():
    assert "yaml" in _needed_extras(_Args(rig=["rig.yaml"]))
    assert "yaml" in _needed_extras(_Args(rig=["rig.yml"]))
    assert "yaml" not in _needed_extras(_Args(rig=["rig.toml"]))
    assert "yaml" not in _needed_extras(_Args(rig=[]))


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


def test_forwarded_headers_are_not_trusted(monkeypatch):
    """The peer is the peer: a local caller could otherwise name a fresh address per guess.

    With uvicorn's default, `X-Forwarded-For` from 127.0.0.1 is believed, so the login
    limiter counted each made-up address separately.
    """
    from flyball.rig import Rig

    seen = {}
    monkeypatch.setattr("uvicorn.Server.run", lambda self: seen.setdefault("config", self.config))
    runner.serve(Rig("t"), RunnerConfig(port=1, log_level="warning"))
    assert seen["config"].proxy_headers is False


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

    def test_a_fronted_runner_ignores_the_file_s_root_path(self, tmp_path, capsys):
        # A rig file's own runner.root_path would put the runner out of step with its
        # front (flyball run always serves at "/"; flyballd passes its own --root-path
        # from the manifest) -- a 404 on the handshake (docs pass, 23 Sep).
        section = RunnerConfig(root_path="/from-the-file")
        s = runner.settle(section, self.parse("--front-dir", str(tmp_path)), tmp_path / "r.yaml")
        assert s.root_path is None
        err = capsys.readouterr().err
        assert "root_path" in err and "ignored" in err

    def test_a_fronted_runner_still_takes_root_path_from_the_front_itself(self, tmp_path):
        # flyballd passes --root-path explicitly (its manifest's, not the file's): that one
        # is authoritative, matching what the front proxies to.
        section = RunnerConfig(root_path="/from-the-file")
        args = self.parse("--front-dir", str(tmp_path), "--root-path", "/from-flyballd")
        s = runner.settle(section, args, tmp_path / "r.yaml")
        assert s.root_path == "/from-flyballd"

    def test_a_bare_runner_still_takes_root_path_from_the_file(self, tmp_path):
        section = RunnerConfig(root_path="/from-the-file")
        s = runner.settle(section, self.parse(), tmp_path / "r.yaml")
        assert s.root_path == "/from-the-file"

    def test_the_auth_section_settles_like_the_rest(self, tmp_path, monkeypatch):
        for var in ("FLYBALL_TOKEN", "FLYBALL_PASSWORD", "FLYBALL_ANONYMOUS", "FLYBALL_SESSION"):
            monkeypatch.delenv(var, raising=False)
        section = RunnerConfig(auth={"token": "filed", "anonymous": "read"})
        s = runner.settle(section, self.parse(), tmp_path / "r.yaml")
        assert (s.auth.token, s.auth.anonymous) == ("filed", "read")
        s = runner.settle(section, self.parse("--anonymous", "none"), tmp_path / "r.yaml")
        assert s.auth.anonymous == "none" and not RunnerConfig().auth.enabled

    def test_a_removed_flag_is_parsed_and_counts_as_absent(self, tmp_path, monkeypatch):
        """D-028: `--password` and friends still parse, so an old unit file still starts."""
        for var in ("FLYBALL_TOKEN", "FLYBALL_PASSWORD", "FLYBALL_SESSION"):
            monkeypatch.delenv(var, raising=False)
        args = self.parse("--password", "hunter2", "--session", "30m")
        s = runner.settle(None, args, tmp_path / "r.yaml")
        assert (s.auth.password, s.auth.session) == ("hunter2", "30m")
        assert not s.auth.enabled, "a password alone opens nothing: the runner is open"
        assert s.auth.removed == ["password", "session"]
        monkeypatch.setenv("FLYBALL_PASSWORD", "env")
        assert runner.settle(None, self.parse(), tmp_path / "r.yaml").auth.password == "env"
        assert RunnerConfig(auth={"secret": "x"}).auth.removed == ["secret"]

    def test_the_token_comes_from_a_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
        (tmp_path / "token").write_text("t0k\n")
        args = self.parse("--token-file", str(tmp_path / "token"))
        assert runner.settle(None, args, tmp_path / "r.yaml").auth.token == "t0k"
        monkeypatch.setenv("FLYBALL_TOKEN", "env")
        assert runner.settle(None, args, tmp_path / "r.yaml").auth.token == "t0k", "the flag wins"

    def test_an_unreadable_token_file_lets_no_one_in(self, tmp_path, monkeypatch, capsys):
        """A bad auth setting narrows exposure, never operation: nobody knows the token."""
        monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
        args = self.parse("--token-file", str(tmp_path / "missing"))
        s = runner.settle(None, args, tmp_path / "r.yaml")
        assert s.auth.enabled and len(s.auth.token or "") >= 32
        assert "missing" in capsys.readouterr().err

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

    def test_run_is_read_as_front_for_one_release(self, tmp_path, caplog):
        # `runner.run` was `flyball run`'s (--serve-ui/--uv); `runner.front` replaces it.
        with caplog.at_level("WARNING"):
            section = RunnerConfig(run={"serve_ui": ":8000", "uv": True, "port": 1})
        assert "runner.front" in caplog.text
        assert section.front is not None and (section.front.listen, section.front.uv) == (
            ":8000",
            True,
        )
        s = runner.settle(section, self.parse(), tmp_path / "r.yaml")
        assert s.front == section.front
        both = RunnerConfig(run={"serve_ui": ":1"}, front={"listen": "127.0.0.1:9"})
        assert both.front is not None and both.front.listen == "127.0.0.1:9", "front wins"
        assert RunnerConfig().run == {} and RunnerConfig().front is None

    def test_a_bad_front_never_stops_the_runner(self, caplog):
        """D-028: `runner.front` is the front's; the strict check is `flyball rig check`'s."""
        with caplog.at_level("WARNING"):
            section = RunnerConfig(front={"auth": "sso-typo", "listen": 5, "nope": 1})
        assert section.front is None
        assert "runner.front" in caplog.text and "nope" in caplog.text
        schema = RunnerConfig.model_json_schema()
        assert "FrontConfig" in schema["$defs"] and "front" in schema["properties"]
        full = RunnerConfig(
            front={
                "listen": "unix:/run/flyball/front.sock",
                "auth": "proxy",
                "url": "https://pi.lab:8443",
                "tls": {"cert": "/c.pem", "key": "/k.pem"},
                "anonymous": "read",
                "proxy": {"preset": "authelia", "from": ["10.0.0.0/8"], "grants": {"all": ["ben"]}},
            }
        )
        assert full.front is not None and full.front.proxy is not None
        assert full.front.proxy.from_ == ["10.0.0.0/8"]

    def test_front_accepts_a_tokens_block(self):
        """`runner.front.tokens`: shaped and forbidden-unknown here; Go validates the values."""
        section = RunnerConfig(
            front={"tokens": {"default_lifetime": "90d", "max_lifetime": "365d"}}
        )
        assert section.front is not None and section.front.tokens is not None
        assert (section.front.tokens.default_lifetime, section.front.tokens.max_lifetime) == (
            "90d",
            "365d",
        )
        # Documented, not validated here: an out-of-range or nonsense value still parses.
        loose = RunnerConfig(front={"tokens": {"max_lifetime": "nonsense"}})
        assert loose.front is not None and loose.front.tokens.max_lifetime == "nonsense"

    def test_a_bad_tokens_block_never_stops_the_runner(self, caplog):
        """D-028, same wrap-validator as a bad `front` block.

        An unknown key inside `tokens` does not stop the runner -- `front` falls back to
        None with a warning.
        """
        with caplog.at_level("WARNING"):
            section = RunnerConfig(front={"tokens": {"default_lifetime": "90d", "nope": 1}})
        assert section.front is None
        assert "runner.front" in caplog.text and "nope" in caplog.text


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
    monkeypatch.setattr("sys.orig_argv", ["python3", "flyball-runner", "rig.yaml", "--port", "1"])
    runner.serve(Rig("t"), RunnerConfig(port=1, log_level="warning"))
    import sys

    assert execs == [
        (sys.executable, [sys.executable, "flyball-runner", "rig.yaml", "--port", "1"])
    ]
    assert deps.current_runner() is None


SIM_LINK = {"type": "sim_plant", "model": "lag", "tau_s": 1.0, "gain": 1.0}


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
        db.execute("DROP TABLE audit")  # 0012's, likewise (its index and triggers go with it)
        for column in ("kind", "origin_ns", "pinned", "continues", "bytes"):
            db.execute(f"ALTER TABLE session DROP COLUMN {column}")
        db.execute("DROP TABLE rig_head")
        db.execute("ALTER TABLE rig_version DROP COLUMN parent_id")
        # 0013's renames, undone: the columns as 0011 knew them
        db.execute("ALTER TABLE tick RENAME COLUMN measured TO reading")
        db.execute("ALTER TABLE tick RENAME COLUMN output TO demand")
        db.execute("ALTER TABLE controller RENAME COLUMN measured TO source")
        db.execute("ALTER TABLE signal RENAME COLUMN warning TO warn")  # 0014's, likewise
        db.execute("ALTER TABLE event RENAME COLUMN code TO kind")  # 0019's, likewise
        db.execute("ALTER TABLE event DROP COLUMN edge")  # 0020's, likewise
        db.execute("DROP TABLE live_value")  # 0023's, likewise
        db.execute("DROP TABLE latch")  # 0025's, likewise
        old = {"controllers": {"h.power": {"signal": "p.t", "default": True}, "h.fan": {}}}
        for i in (1, 2, 3):
            db.execute(
                "INSERT INTO rig_version (time_ns, reason, files, document) VALUES (?, ?, '[]', ?)",
                (i, f"v{i}", json.dumps(old if i == 3 else {})),
            )
        db.execute("UPDATE schema_version SET version = 8")
    store = SqliteStore(path)
    rows = store.rig_versions()
    assert [(r.id, r.parent) for r in rows] == [(3, 2), (2, 1), (1, None)]
    assert store.head_rig_version().id == 3
    # 0013: a stored controller's `signal` is its `measured` now, so the version still loads
    assert rows[0].document == {
        "controllers": {"h.power": {"default": True, "measured": "p.t"}, "h.fan": {}}
    }
    assert rows[2].document == {}
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
            (["--token", "t0k"], {}),
            ([], {"FLYBALL_TOKEN": "t0k"}),
        ],
    )
    def test_a_token_lets_it_bind_anywhere(self, lab, served, monkeypatch, argv, env):
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
        for needed in ("127.0.0.1:8123", "token", "--insecure-open"):
            assert needed in moved.warning, needed
        assert "FLYBALL_INSECURE_OPEN" in moved.warning
        assert settle_exposure(RunnerConfig(host="::")).host == "127.0.0.1"
        opened = settle_exposure(RunnerConfig(host="0.0.0.0"), insecure_open=True)
        assert opened.host == "0.0.0.0" and opened.open_network and not opened.restricted
        assert opened.warning is not None and "anyone" in opened.warning
        clear = settle_exposure(RunnerConfig(host="0.0.0.0", auth=AuthConfig(token="x")))
        assert clear.host == "0.0.0.0" and not clear.open and not clear.open_network
        assert clear.warning is not None and "unencrypted" in clear.warning
        assert clear.endpoint == "tcp:0.0.0.0:8000"
        assert settle_exposure(RunnerConfig(auth=AuthConfig(token="x"))).warning is None
        # A password alone is ignored: the runner is open, so it serves loopback.
        ignored = settle_exposure(RunnerConfig(host="0.0.0.0", auth=AuthConfig(password="x")))
        assert ignored.open and ignored.host == "127.0.0.1" and len(ignored.notes) == 1
        assert ignored.warning is not None and "removed and ignored" in ignored.warning
        assert "removed and ignored" in ignored.as_dict()["notes"][0]
        # Fronted: the endpoint only, and runner.auth ignored with a note.
        fronted = settle_exposure(
            RunnerConfig(host="0.0.0.0", auth=AuthConfig(token="x")), endpoint="unix:/r/sock"
        )
        assert (fronted.endpoint, fronted.fronted, fronted.open) == ("unix:/r/sock", True, False)
        assert fronted.notes and "--front-dir" in fronted.notes[0]
        assert settle_exposure(RunnerConfig(), endpoint="unix:/r/sock").notes == ()

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

    def test_a_removed_password_warns_once_and_serves_loopback(self, monkeypatch, capsys):
        from flyball.rig import Rig
        from flyball.runtime.config import AuthConfig

        bound: list = []
        monkeypatch.setattr("uvicorn.Server.run", lambda self: bound.append(self.config.host))
        runner.serve(Rig("t"), RunnerConfig(host="0.0.0.0", port=1, auth=AuthConfig(password="p")))
        assert bound == ["127.0.0.1"]
        lines = [line for line in capsys.readouterr().err.splitlines() if "WARNING" in line]
        assert len(lines) == 1 and "removed and ignored" in lines[0], lines


def test_a_bare_runner_with_a_token_prints_its_link(monkeypatch, capsys):
    from flyball.rig import Rig
    from flyball.runtime.config import AuthConfig

    seen = {}
    monkeypatch.setattr("uvicorn.Server.run", lambda self: seen.setdefault("app", self.config.app))
    runner.serve(Rig("t"), RunnerConfig(port=8123, root_path="/r", auth=AuthConfig(token="x")))
    err = capsys.readouterr().err
    assert "http://127.0.0.1:8123/r/api/auth/link?n=" in err
    nonce = err.split("link?n=")[1].split()[0]
    assert seen["app"].state.door.take_link(nonce)
    runner.serve(Rig("t"), RunnerConfig(port=8123))
    assert "link?n=" not in capsys.readouterr().err, "an open runner needs no link"


# region Fronted: the front-dir

KEY_HEX = "0a1b2c3d" * 8


def front_dir(root: Path, **files: str) -> Path:
    """A front-dir as the front writes it: 0700, three 0600 files."""
    import os

    root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    defaults = {"key": KEY_HEX + "\n", "aud": "run-0a1b2c3d\n", "endpoint": f"unix:{root}/sock\n"}
    for name, text in {**defaults, **files}.items():
        if text is None:
            continue
        path = root / name
        path.write_text(text)
        os.chmod(path, 0o600)
    return root


def test_a_front_dir_is_read(tmp_path, monkeypatch):
    from flyball.runner.frontdir import read

    got = read(front_dir(tmp_path / "f"))
    assert (got.key, got.aud) == (bytes.fromhex(KEY_HEX), "run-0a1b2c3d")
    assert (got.network, got.address) == ("unix", f"{tmp_path}/f/sock")
    monkeypatch.setattr(sys, "platform", "win32")  # tcp is Windows-only (D-044)
    tcp = read(front_dir(tmp_path / "t", endpoint="tcp:127.0.0.1:8102\n"))
    assert (tcp.network, tcp.host, tcp.port) == ("tcp", "127.0.0.1", 8102)
    assert tcp.endpoint == "tcp:127.0.0.1:8102"


@pytest.mark.parametrize(
    "files, why",
    [
        ({"key": None}, "key"),
        ({"aud": None}, "aud"),
        ({"endpoint": None}, "endpoint"),
        ({"key": KEY_HEX.upper() + "\n"}, "key"),
        ({"key": KEY_HEX[:-2] + "\n"}, "key"),
        ({"aud": "\n"}, "aud"),
        ({"aud": "two words\n"}, "aud"),
        ({"endpoint": "unix:relative/sock\n"}, "endpoint"),
        ({"endpoint": "tcp:192.168.1.3:8102\n"}, "endpoint"),
        ({"endpoint": "tcp:127.0.0.1:0\n"}, "endpoint"),
        ({"endpoint": "unix:/" + "x" * 120 + "\n"}, "endpoint"),
        ({"endpoint": "http://127.0.0.1:1\n"}, "endpoint"),
    ],
)
def test_a_front_dir_missing_or_malformed_is_unusable(tmp_path, monkeypatch, files, why):
    from flyball.runner.frontdir import Unusable, read

    monkeypatch.setattr(sys, "platform", "win32")  # a malformed tcp endpoint, not D-044's refusal
    with pytest.raises(Unusable, match=why) as refused:
        read(front_dir(tmp_path / "f", **files))
    assert "D-044" not in str(refused.value)


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_a_tcp_front_dir_is_refused_off_windows(tmp_path, monkeypatch, platform):
    from flyball.runner.frontdir import Unusable, read

    monkeypatch.setattr(sys, "platform", platform)
    with pytest.raises(Unusable, match=r"tcp is refused except on Windows .*D-044.*unix socket"):
        read(front_dir(tmp_path / "f", endpoint="tcp:127.0.0.1:8102\n"))
    assert read(front_dir(tmp_path / "u")).network == "unix"


def test_an_unsafe_front_dir_is_unusable(tmp_path):
    import os

    from flyball.runner.frontdir import Unusable, read

    loose = front_dir(tmp_path / "loose")
    os.chmod(loose, 0o755)
    with pytest.raises(Unusable, match="0700"):
        read(loose)
    real = front_dir(tmp_path / "real")
    (tmp_path / "link").symlink_to(real)
    with pytest.raises(Unusable, match="symlink"):
        read(tmp_path / "link")
    shared = front_dir(tmp_path / "shared")
    os.chmod(shared / "key", 0o644)
    with pytest.raises(Unusable, match="key"):
        read(shared)
    with pytest.raises(Unusable, match="no such"):
        read(tmp_path / "absent")


def test_main_exits_4_before_the_lock_on_a_bad_front_dir(tmp_path, monkeypatch, capsys):
    rig_file = tmp_path / "lab.yaml"
    rig_file.write_text("name: lab\n")
    store = tmp_path / "s.sqlite"
    monkeypatch.setattr("flyball.runner.entrypoint.serve", lambda *a, **kw: None)
    bad = front_dir(tmp_path / "f", key=None)
    argv = [str(rig_file), "--store", str(store), "--front-dir", str(bad)]
    assert runner.main(argv) == 4
    assert "--front-dir" in capsys.readouterr().err
    assert not (tmp_path / "s.sqlite.lock").exists(), "before the rig's lock"


def test_main_serves_fronted_and_holds_the_runner_lock(tmp_path, monkeypatch):
    import fcntl

    rig_file = tmp_path / "lab.yaml"
    rig_file.write_text("name: lab\n")
    folder = front_dir(tmp_path / "f")
    seen: dict = {}

    def serve(rig, settings, **kw):
        seen.update(kw)
        with open(folder / "runner.lock") as held, pytest.raises(BlockingIOError):
            fcntl.flock(held, fcntl.LOCK_SH | fcntl.LOCK_NB)
        seen["lock"] = (folder / "runner.lock").read_text()

    monkeypatch.setattr("flyball.runner.entrypoint.serve", serve)
    argv = [str(rig_file), "--store", str(tmp_path / "s.sqlite"), "--front-dir", str(folder)]
    assert runner.main(argv) == 0
    assert seen["front"].aud == "run-0a1b2c3d"
    import os

    assert seen["lock"] == f"pid {os.getpid()} rig lab\n"
    assert oct((folder / "runner.lock").stat().st_mode & 0o777) == "0o600"


def test_main_holds_the_runner_lock_before_it_reads_the_key(tmp_path, monkeypatch):
    """`runner.lock` is held from the moment the key is read.

    Or a front that starts while this runner is starting rewrites the key under a runner that
    already read the old one (then spawns a second runner: exit 3, the rig busy for ever).
    """
    import fcntl

    from flyball.runner import frontdir

    rig_file = tmp_path / "lab.yaml"
    rig_file.write_text("name: lab\n")
    folder = front_dir(tmp_path / "f")
    real = frontdir.read
    seen: dict = {}

    def read(path):
        with open(folder / "runner.lock", "a") as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)
                seen["held"] = False
            except BlockingIOError:
                seen["held"] = True
        return real(path)

    monkeypatch.setattr(frontdir, "read", read)
    monkeypatch.setattr("flyball.runner.entrypoint.serve", lambda *a, **kw: None)
    argv = [str(rig_file), "--store", str(tmp_path / "s.sqlite"), "--front-dir", str(folder)]
    assert runner.main(argv) == 0
    assert seen == {"held": True}, "the key was read before runner.lock was taken"


def test_a_held_runner_lock_is_exit_3_before_the_key_is_read(tmp_path, monkeypatch, capsys):
    """Another runner lives in the front-dir: this one leaves at once, whatever the files say."""
    import fcntl

    rig_file = tmp_path / "lab.yaml"
    rig_file.write_text("name: lab\n")
    folder = front_dir(tmp_path / "f", key="not a key\n")
    monkeypatch.setattr("flyball.runner.entrypoint.serve", lambda *a, **kw: None)
    monkeypatch.setattr("flyball.runner.locking.FRONT_PATIENCE_S", 0.05, raising=False)
    argv = [str(rig_file), "--store", str(tmp_path / "s.sqlite"), "--front-dir", str(folder)]
    with open(folder / "runner.lock", "a") as live:
        fcntl.flock(live, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert runner.main(argv) == 3
    assert "another runner lives in" in capsys.readouterr().err
    assert not (tmp_path / "s.sqlite.lock").exists(), "nor took the rig's lock"


def test_hold_front_waits_out_a_front_s_probe(tmp_path):
    """A runner taking `runner.lock` while a front probes it does not give up (exit 3).

    A front asks "is a runner here?" with a shared lock held for an instant.
    """
    import fcntl
    import threading

    from flyball.runner import locking

    folder = front_dir(tmp_path / "f")
    probe = open(folder / "runner.lock", "a")  # noqa: SIM115 -- released by the timer
    fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)
    timer = threading.Timer(0.2, probe.close)
    timer.start()
    try:
        with locking.hold_front(folder, "lab"):
            pass
    finally:
        timer.cancel()
        probe.close()


def test_hold_front_still_refuses_a_live_runner(tmp_path, monkeypatch):
    import fcntl

    from flyball.runner import locking

    monkeypatch.setattr(locking, "FRONT_PATIENCE_S", 0.1, raising=False)
    folder = front_dir(tmp_path / "f")
    with open(folder / "runner.lock", "a") as live:
        fcntl.flock(live, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(locking.RigBusy, match="another runner lives in"):
            locking.hold_front(folder, "lab")


def test_the_store_lock_is_owner_only_from_its_creation_and_holds_no_secret(tmp_path, monkeypatch):
    import fcntl
    import os
    import stat

    from flyball.runner import locking

    argv = ["flyball-runner", "rig.yaml", "--token", "s3cret", "--password=hunter2"]
    argv += ["--set", "runner.auth.token=t0ken", "--port", "8100"]
    monkeypatch.setattr(sys, "argv", argv)
    modes = []
    real = fcntl.flock

    def flock(handle, how):
        modes.append(stat.S_IMODE(os.fstat(handle.fileno()).st_mode))
        return real(handle, how)

    monkeypatch.setattr(locking.fcntl, "flock", flock)
    old = os.umask(0o022)
    try:
        held = locking.hold(tmp_path / "s.sqlite")
    finally:
        os.umask(old)
    with held:
        assert modes and modes[0] == 0o600, f"created {oct(modes[0])} before the chmod"
        text = (tmp_path / "s.sqlite.lock").read_text()
        with pytest.raises(locking.RigBusy) as busy:
            locking.hold(tmp_path / "s.sqlite")
    for secret in ("s3cret", "hunter2", "t0ken"):
        assert secret not in text and secret not in str(busy.value)
    assert "--port 8100" in text and "--token ***" in text


def test_the_restart_log_line_holds_no_secret(monkeypatch, caplog):
    from flyball.rig import Rig

    def fake_run(self):
        from flyball.interfaces.server import deps

        deps.current_runner().restart()

    monkeypatch.setattr("uvicorn.Server.run", fake_run)
    monkeypatch.setattr("os.execv", lambda exe, argv: None)
    monkeypatch.setattr("sys.orig_argv", ["python3", "flyball-runner", "--token", "s3cret"])
    with caplog.at_level("INFO", logger="flyball.runner"):
        runner.serve(Rig("t"), RunnerConfig(port=1, log_level="warning"))
    assert "restarting" in caplog.text and "s3cret" not in caplog.text


def test_serve_fronted_binds_the_endpoint_only(tmp_path, monkeypatch, capsys):
    import os
    import socket
    import stat

    from flyball.rig import Rig
    from flyball.runner.frontdir import read
    from flyball.runtime.config import AuthConfig

    seen = {}

    def run(self):
        sock = socket.socket(fileno=os.dup(self.config.fd))
        with sock:
            seen.update(config=self.config, name=sock.getsockname())
        seen["mode"] = stat.S_IMODE(os.lstat(tmp_path / "f" / "sock").st_mode)

    monkeypatch.setattr("uvicorn.Server.run", run)
    front = read(front_dir(tmp_path / "f"))
    settings = RunnerConfig(host="0.0.0.0", port=9, auth=AuthConfig(token="x", anonymous="read"))
    runner.serve(Rig("t"), settings, front=front)
    config = seen["config"]
    assert seen["name"] == f"{tmp_path}/f/sock" and seen["mode"] == 0o600
    assert config.uds is None and config.fd is not None
    assert not (tmp_path / "f" / "sock").exists(), "removed after serving"
    assert config.app.state.door.fronted is not None and config.app.state.door.aud == front.aud
    lines = [line for line in capsys.readouterr().err.splitlines() if "WARNING" in line]
    assert len(lines) == 1 and "--front-dir" in lines[0] and "link?n=" not in lines[0]
    monkeypatch.setattr(sys, "platform", "win32")  # tcp is Windows-only (D-044)
    tcp = read(front_dir(tmp_path / "t", endpoint="tcp:127.0.0.1:8102\n"))
    monkeypatch.setattr("uvicorn.Server.run", lambda self: seen.update(config=self.config))
    runner.serve(Rig("t"), RunnerConfig(), front=tcp)
    assert (seen["config"].host, seen["config"].port, seen["config"].uds) == (
        "127.0.0.1",
        8102,
        None,
    )


def test_the_runner_route_reports_the_endpoint():
    from conftest import FakeRunner, TestClient
    from flyball.interfaces.server import create_app
    from flyball.interfaces.server.deps import set_runner
    from flyball.runtime.config import settle_exposure

    fake = FakeRunner()
    fake.exposure = settle_exposure(RunnerConfig(), endpoint="unix:/r/sock")
    set_runner(fake)
    try:
        with TestClient(create_app()) as http:
            body = http.get("/api/runner").json()
        assert body["endpoint"] == "unix:/r/sock" and "host" not in body and "port" not in body
    finally:
        set_runner(None)


# endregion


# region After a rig edit (D-051)

LAB = """\
name: lab
links:
  t1: {type: sim_plant, model: lag, tau_s: 1.0}
devices:
  probe:
    driver: sim_daq
    link: t1
    poll_s: 0.1
    ports: {signal: {port: output, quantity: x, unit: "1"}}
  drive: {driver: sim_drive, link: t1, ports: {u: input}}
"""


def test_a_restart_for_an_edit_of_a_bare_rig_resumes_from_the_store(monkeypatch):
    from flyball.interfaces.server import deps
    from flyball.rig import Rig
    from flyball.runner.serving import EDIT_ENV

    execs = []

    def fake_run(self):
        deps.current_runner().restart_for_edit(7, 6, record=True)

    monkeypatch.setattr("uvicorn.Server.run", fake_run)
    monkeypatch.setattr("os.execv", lambda exe, argv: execs.append(argv))
    monkeypatch.setattr("sys.orig_argv", ["python3", "flyball-runner", "--port", "1"])
    monkeypatch.delenv(EDIT_ENV, raising=False)
    runner.serve(Rig("t"), RunnerConfig(port=1, log_level="warning"))
    assert execs == [[sys.executable, "flyball-runner", "--port", "1", "--resume"]]
    import os

    assert os.environ.pop(EDIT_ENV) == "7:6:1"


def test_a_restart_for_an_edit_of_a_rig_file_keeps_the_command_line(monkeypatch, tmp_path):
    from flyball.interfaces.server import deps
    from flyball.rig import Rig
    from flyball.runner.serving import EDIT_ENV
    from flyball.runtime.edits import Origin

    execs = []
    monkeypatch.setattr(
        "uvicorn.Server.run", lambda self: deps.current_runner().restart_for_edit(3, None)
    )
    monkeypatch.setattr("os.execv", lambda exe, argv: execs.append(argv))
    monkeypatch.setattr("sys.orig_argv", ["python3", "flyball-runner", "lab.yaml"])
    monkeypatch.delenv(EDIT_ENV, raising=False)
    origin = Origin((tmp_path / "lab.yaml",))
    runner.serve(Rig("t"), RunnerConfig(port=1, log_level="warning"), origin=origin)
    assert execs == [[sys.executable, "flyball-runner", "lab.yaml"]], "the overlay holds it"
    import os

    assert os.environ.pop(EDIT_ENV) == "3::"


def test_an_edit_that_does_not_build_is_rolled_back_and_said(tmp_path, monkeypatch, caplog):
    import os

    import yaml

    from flyball.record.sqlite import SqliteStore
    from flyball.runner.serving import EDIT_ENV, EDIT_FAILED_ENV

    rig_file = tmp_path / "lab.yaml"
    rig_file.write_text(LAB)
    store_path = tmp_path / "s.sqlite"
    argv = [str(rig_file), "--store", str(store_path), "--no-mcp"]
    seen: dict = {}
    monkeypatch.setattr(
        "flyball.runner.entrypoint.serve", lambda rig, settings, **kw: seen.update(rig=rig)
    )
    monkeypatch.delenv(EDIT_ENV, raising=False)
    monkeypatch.delenv(EDIT_FAILED_ENV, raising=False)
    assert runner.main(argv) == 0
    seen.pop("rig").close()
    store = SqliteStore(store_path)
    started = store.head_rig_version()
    assert started is not None and started.reason == "loaded"
    # An edit saved: a controller on a signal the drive does not have. It validates (an
    # address is only checked for its dot) and fails at the build.
    overlay = tmp_path / "lab.yaml.d" / "added.yaml"
    overlay.parent.mkdir()
    broken = {"controllers": {"drive.nope": {"measured": "probe.signal"}}}
    overlay.write_text(yaml.safe_dump(broken))
    edited = store.save_rig_version(1, "edited: x", {"name": "lab"}, [])
    store.close()
    monkeypatch.setenv(EDIT_ENV, f"{edited.id}:{started.id}:")

    class Exec(Exception):
        pass

    def execv(exe, args):
        raise Exec(args)

    monkeypatch.setattr("os.execv", execv)
    monkeypatch.setattr("sys.orig_argv", ["python3", "flyball-runner", *argv])
    with pytest.raises(Exec):
        runner.main(argv)
    assert not overlay.exists(), "there was no overlay before the edit: none now"
    assert EDIT_ENV not in os.environ
    failed = json.loads(os.environ[EDIT_FAILED_ENV])
    assert failed["version"] == edited.id and failed["previous"] == started.id
    assert "nope" in failed["error"]
    store = SqliteStore(store_path)
    head = store.head_rig_version()
    assert head is not None and head.reason == f"restored from {started.id}"
    assert head.document == started.document
    store.close()
    # The start after the rollback: the rig before the edit, and a condition saying why.
    assert runner.main(argv) == 0
    rig = seen.pop("rig")
    try:
        assert EDIT_FAILED_ENV not in os.environ, "said once"
        held = [c for c in rig.conditions.all() if c.code == "edit_not_built"]
        assert len(held) == 1 and held[0].subject_kind == "rig"
        assert held[0].message.startswith(f"edit to version {edited.id} did not build: ")
        assert held[0].message.endswith(f"; running version {started.id}")
        assert "drive.nope" not in rig.controllers
    finally:
        rig.close()


def test_a_start_says_what_the_saved_overlay_sets(tmp_path, monkeypatch, caplog):
    import os
    import time

    rig_file = tmp_path / "lab.yaml"
    rig_file.write_text(LAB)
    overlay = tmp_path / "lab.yaml.d" / "added.yaml"
    overlay.parent.mkdir()
    overlay.write_text("devices:\n  probe: {label: Probe}\n")
    later = time.time() + 5
    os.utime(rig_file, (later, later))  # the file was edited after the overlay was saved
    seen: dict = {}
    monkeypatch.setattr(
        "flyball.runner.entrypoint.serve", lambda rig, settings, **kw: seen.update(rig=rig)
    )
    caplog.set_level("INFO", logger="flyball.runner")
    assert runner.main([str(rig_file), "--store", str(tmp_path / "s.sqlite"), "--no-mcp"]) == 0
    seen.pop("rig").close()
    assert f"saved overlay {overlay} sets devices.probe" in caplog.text
    assert "was changed after the saved overlay" in caplog.text and "devices.probe" in caplog.text


# endregion
