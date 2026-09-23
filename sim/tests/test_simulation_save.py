"""`Simulation.save` replaces the rig file atomically: a unique temp file, fsynced, then renamed."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from flyball.runtime.config import load_rig_config, resolve_document

from flyball_sim.simulation import Simulation

RIG = {
    "name": "oven",
    "links": {"chamber": {"type": "sim_plant", "model": "lag", "tau_s": 10.0, "gain": 50.0}},
    "devices": {
        "thermocouple": {
            "driver": "sim_daq",
            "config": {
                "link": "chamber",
                "ports": {
                    "temperature": {"port": "output", "quantity": "temperature", "unit": "°C"}
                },
            },
        },
        "heater": {
            "driver": "sim_drive",
            "config": {"link": "chamber", "ports": {"drive": "input"}},
        },
    },
}


@pytest.fixture
def oven(tmp_path: Path) -> Simulation:
    path = tmp_path / "oven.json"
    path.write_text(json.dumps(RIG))
    document, _ = resolve_document(path)
    config = load_rig_config(path)
    return Simulation(config.build(start=False), config, document, path)


def _saved_gain(path: Path) -> float:
    return json.loads(path.read_text())["links"]["chamber"]["gain"]


def test_save_writes_the_change_and_leaves_no_temp_file(oven, tmp_path):
    oven.set_plant("chamber", gain=40.0)
    assert oven.save() == tmp_path / "oven.json"
    assert _saved_gain(tmp_path / "oven.json") == 40.0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["oven.json"]


def test_another_writers_temp_file_is_not_clobbered(oven, tmp_path):
    """The temp name is unique: a file that happens to be at `<name>.tmp` is someone else's."""
    theirs = tmp_path / "oven.json.tmp"
    theirs.write_text("not ours")
    oven.set_plant("chamber", gain=40.0)
    oven.save()
    assert theirs.read_text() == "not ours"


def test_a_failed_rename_keeps_the_old_file_and_cleans_up(oven, tmp_path, monkeypatch):
    def refuse(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    oven.set_plant("chamber", gain=40.0)
    with pytest.raises(OSError, match="disk full"):
        oven.save()
    assert _saved_gain(tmp_path / "oven.json") == 50.0, "the old file is untouched"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["oven.json"], "no temp file left"
    assert oven.describe()["changed"] == ["links.chamber"], "still unsaved"


def test_the_file_and_its_directory_are_fsynced(oven, tmp_path, monkeypatch):
    synced: list[str] = []
    real_fsync = os.fsync

    def fsync(fd: int) -> None:
        synced.append(os.readlink(f"/proc/self/fd/{fd}"))
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    oven.save()
    assert len(synced) == 2
    assert Path(synced[0]).parent == tmp_path and Path(synced[0]).name != "oven.json", "the temp"
    assert Path(synced[1]) == tmp_path, "then the directory, so the rename survives a crash"


def test_atomic_write_text_keeps_an_existing_files_mode(tmp_path):
    from flyball.foundation.files import atomic_write_text

    target = tmp_path / "rig.yaml"
    target.write_text("old")
    target.chmod(0o640)
    atomic_write_text(target, "new")
    assert target.read_text() == "new"
    assert target.stat().st_mode & 0o777 == 0o640
