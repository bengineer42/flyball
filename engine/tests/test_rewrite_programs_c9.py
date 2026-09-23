"""The one-off stored-programs rewrite (scripts/rewrite_programs_c9.py), against a temp store."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType

import pytest

from flyball.interfaces.server.dialect import Dialect, program_from_document
from flyball.interfaces.server.formats import parse
from flyball.model.catalog import ensure_discovered
from flyball.record.sqlite import SqliteStore
from flyball.sequencing import Prompt, Settle, Wait

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rewrite_programs_c9.py"

OLD_YAML = """# a comment
name: soak
steps:
  - regulate: 50
  - arrive: {within: 1, readings: 5, minutes: 8}
  - hold: {minutes: 20, message: "soak", timeout: 1500}
  - wait: "unload, then press go"
  - wait: {message: "really?", name: sure, minutes: 10}
"""

OLD_TOML = """name = "toml"
[[steps]]
wait = { message = "load", timeout = { minutes = 5 } }
[[steps]]
hold = { seconds = 30 }
"""

OLD_JSON = {"steps": [{"arrive": {"loop": "x", "readings": 2}}, {"hold": 60}]}

NEW_YAML = """steps:
  - wait: {duration: {minutes: 5}, message: "already new"}
  - prompt: go
"""


@pytest.fixture
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rewrite_programs_c9", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    path = tmp_path / "rig.sqlite"
    store = SqliteStore(path)
    store.save_program("soak", "yaml", OLD_YAML, 10)
    store.save_program("toml", "toml", OLD_TOML, 20)
    store.save_program("json", "json", json.dumps(OLD_JSON), 30)
    store.save_program("new", "yaml", NEW_YAML, 40)
    store.save_program("broken", "yaml", "steps: [{hold: {minutes: 1, bogus: 2}}]", 50)
    store.close()
    return path


def test_old_programs_get_a_new_version_and_keep_the_old(script, store_path):
    out = io.StringIO()
    assert script.run(store_path, out=out) == 3
    dialect = Dialect(commands=dict(ensure_discovered().commands.items()))
    store = SqliteStore(store_path)
    try:
        for name in ("soak", "toml", "json"):
            history = store.program_history(name)
            assert len(history) == 2, "the old version stays"
            newest = history[0]
            assert newest.label == "c9 rewrite" and newest.created_ns > history[1].created_ns
            assert newest.sha256 == hashlib.sha256(newest.body.encode()).hexdigest()
            assert store.program(name).id == newest.id
            program_from_document(parse(newest.body, newest.format), dialect)

        soak = program_from_document(parse(store.program("soak").body, "yaml"), dialect)
        settle, wait, unload, sure = soak[1], soak[2], soak[3], soak[4]
        assert isinstance(settle, Settle) and settle.count == 5
        assert settle.timeout is not None and settle.timeout.seconds == 480
        assert isinstance(wait, Wait) and wait.duration.seconds == 1200
        assert wait.timeout is not None and wait.timeout.seconds == 1500
        assert isinstance(unload, Prompt) and unload.message == "unload, then press go"
        assert isinstance(sure, Prompt) and sure.name == "sure"
        assert sure.timeout is not None and sure.timeout.seconds == 600

        toml = program_from_document(parse(store.program("toml").body, "toml"), dialect)
        assert [type(c) for c in toml] == [Prompt, Wait]
        assert json.loads(store.program("json").body)["steps"] == [
            {"settle": {"loop": "x", "count": 2}},
            {"wait": 60},
        ]

        assert len(store.program_history("new")) == 1, "already new: left alone"
        assert len(store.program_history("broken")) == 1, "still does not load: not saved"
    finally:
        store.close()
    assert "broken: not saved" in out.getvalue()


def test_a_second_run_saves_nothing_and_a_dry_run_saves_nothing(script, store_path):
    before = SqliteStore(store_path)
    count = len(before.program_history("soak"))
    before.close()
    out = io.StringIO()
    assert script.run(store_path, dry_run=True, out=out) == 0
    assert "soak: would save a new version" in out.getvalue()
    assert script.run(store_path, out=io.StringIO()) == 3
    assert script.run(store_path, out=io.StringIO()) == 0
    after = SqliteStore(store_path)
    try:
        assert len(after.program_history("soak")) == count + 1
    finally:
        after.close()
