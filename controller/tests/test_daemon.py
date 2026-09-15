"""The daemon builds a rig from a file, records if told to, and serves it with a programmer."""

from __future__ import annotations

from pathlib import Path

import pytest

from flyball import daemon
from flyball.runtime.config import load_rig_config

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"


@pytest.fixture
def oven():
    """The oven config; its sources are forgotten after, so it can be built again."""
    from flyball.core.reading import Source

    config = load_rig_config(EXAMPLES / "oven.toml")
    names = [entry.device.name for entry in config.readers]
    for name in names:  # another test may have built it already
        Source.forget(name)
    yield config
    for name in names:
        Source.forget(name)


def test_start_builds_the_rig_and_records_only_when_asked(tmp_path, oven):
    rig = daemon.start(oven)
    try:
        assert rig.name == "oven" and rig.recorder is None and list(rig.loops)
    finally:
        rig.readers.stop_all()


def test_start_records_when_asked(tmp_path, oven):
    rig = daemon.start(oven, record=True, store_path=tmp_path / "s.sqlite")
    try:
        assert rig.recorder is not None and (tmp_path / "s.sqlite").exists()
    finally:
        rig.stop_recording()
        rig.readers.stop_all()


def test_the_file_s_recording_flag_is_the_default(tmp_path, oven):
    config = oven.model_copy(update={"recording": True})
    rig = daemon.start(config, store_path=tmp_path / "s.sqlite")
    try:
        assert rig.recorder is not None
    finally:
        rig.stop_recording()
        rig.readers.stop_all()


def test_an_explicit_no_beats_the_file(tmp_path, oven):
    config = oven.model_copy(update={"recording": True})
    rig = daemon.start(config, record=False, store_path=tmp_path / "t.sqlite")
    try:
        assert rig.recorder is None
    finally:
        rig.readers.stop_all()


def test_a_bad_file_is_a_message_not_a_traceback(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text('name = "x"\n[[actuators]]\ntag = "nope"\n')
    assert daemon.main([str(bad)]) == 2
    assert "nope" in capsys.readouterr().err


def test_serve_attaches_rig_and_programmer_and_detaches_after(monkeypatch):
    from flyball.runtime.rig import Rig
    from flyball.server import deps

    seen = {}

    def fake_run(app, host, port, log_level):
        seen["rig"] = deps.current_rig()
        seen["programmer"] = deps.get_programmer()

    monkeypatch.setattr("uvicorn.run", fake_run)
    rig = Rig("t")
    daemon.serve(rig, "127.0.0.1", 1, "warning")
    assert seen["rig"] is rig and seen["programmer"].rig is rig
    assert deps.current_rig() is None
    with pytest.raises(Exception, match="No programmer"):
        deps.get_programmer()
