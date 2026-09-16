"""Board profiles: found on the board path, merged under the file, pins resolved."""

from __future__ import annotations

import pytest

from flyball.core.config import Config
from flyball.core.errors import NotFoundError
from flyball.runtime.config import (
    BOARDS_ENV,
    Board,
    RigConfig,
    apply_board,
    board_dirs,
    find_board,
    registered,
    resolve_document,
    rig_model,
    role_of,
)

BOARD = """
name = "Test board"
[links.bus]
tag = "fake_registers"
[links.plant]
tag = "sim_plant"
[pins]
OUT1 = { link = "bus", unit_id = 7 }
"""


def test_board_dirs_walk_up_from_the_rig_file(tmp_path, monkeypatch):
    monkeypatch.setenv(BOARDS_ENV, "/first:/second")
    rig_dir = tmp_path / "a" / "b"
    rig_dir.mkdir(parents=True)
    dirs = list(board_dirs(rig_dir))
    assert dirs[:2] == [__import__("pathlib").Path("/first"), __import__("pathlib").Path("/second")]
    assert rig_dir / "boards" in dirs and tmp_path / "boards" in dirs
    assert dirs.index(rig_dir / "boards") < dirs.index(tmp_path / "boards")
    assert str(dirs[-1]) == "/etc/flyball/boards"


def test_find_board_by_name_and_by_path(tmp_path, monkeypatch):
    monkeypatch.delenv(BOARDS_ENV, raising=False)
    (tmp_path / "boards").mkdir()
    (tmp_path / "boards" / "mine.toml").write_text(BOARD)
    rig_dir = tmp_path / "rigs"
    rig_dir.mkdir()
    assert find_board("mine", rig_dir) == tmp_path / "boards" / "mine.toml"
    assert find_board("../boards/mine.toml", rig_dir) == rig_dir / "../boards/mine.toml"
    with pytest.raises(NotFoundError, match="looked in"):
        find_board("other", rig_dir)
    with pytest.raises(NotFoundError, match="does not exist"):
        find_board("nowhere.toml", rig_dir)


def test_apply_board_merges_links_and_resolves_pins():
    board = Board.model_validate(__import__("tomllib").loads(BOARD))
    document = {
        "links": {"bus": {"tag": "fake_registers", "registers": {"1": 5}}},
        "readers": [
            {
                "period_s": 1,
                "device": {"tag": "modbus_reader", "name": "r", "pin": "OUT1", "registers": {}},
            }
        ],
        "actuators": [
            {
                "tag": "modbus_actuator",
                "name": "a",
                "pin": "OUT1",
                "unit_id": 2,
                "output": {"address": 1},
            },
        ],
    }
    out = apply_board(document, board)
    assert out["links"]["bus"] == {"tag": "fake_registers", "registers": {"1": 5}}, "file wins"
    assert out["links"]["plant"] == {"tag": "sim_plant"}
    device = out["readers"][0]["device"]
    assert device["link"] == "bus" and device["unit_id"] == 7 and "pin" not in device
    assert out["actuators"][0]["unit_id"] == 2, "an entry's own field wins over the pin's"
    assert document["readers"][0]["device"]["pin"] == "OUT1", "the input is untouched"
    with pytest.raises(NotFoundError, match="not on this board"):
        apply_board({"actuators": [{"pin": "NOPE"}]}, board)


def test_a_rig_file_with_a_board_validates_and_reports_it(tmp_path, monkeypatch):
    monkeypatch.setenv(BOARDS_ENV, str(tmp_path / "profiles"))
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "test.toml").write_text(BOARD)
    rig = tmp_path / "rig.toml"
    rig.write_text(
        'board = "test"\n'
        '[[actuators]]\ntag = "modbus_actuator"\nname = "valve"\npin = "OUT1"\n'
        "[actuators.output]\naddress = 1\n"
    )
    document, board_path = resolve_document(rig)
    assert board_path == tmp_path / "profiles" / "test.toml"
    config = RigConfig.model_validate(document)
    assert config.board == "test" and set(config.links) == {"bus", "plant"}
    assert config.actuators[0].link == "bus" and config.actuators[0].unit_id == 7


def test_roles_come_from_what_build_returns():
    assert role_of(Config.registry["scpi_reader"]) == "reader"
    assert role_of(Config.registry["sim_actuator"]) == "actuator"
    assert role_of(Config.registry["visa"]) == "link"
    assert Config.registry["sim_reader"] in registered("reader")


def test_a_tag_registered_later_is_valid_in_a_file(fresh):
    from flyball.core.device import DeviceConfig
    from flyball.core.reading import Reader, Source

    tag = fresh("late_reader")
    before = rig_model()

    class Late(Reader):
        """Registered after the module was imported."""

        def __init__(self, name):
            super().__init__(name, (Source(name, ()),))

        def read(self, time_ns):
            return []

    class LateConfig(DeviceConfig[Late], tag=tag):
        name: str

        def build(self) -> Late:
            return Late(self.name)

    config = RigConfig.model_validate({"readers": [{"device": {"tag": tag, "name": fresh("x")}}]})
    assert isinstance(config, RigConfig) and isinstance(config.readers[0].device, LateConfig)
    assert rig_model() is not before, "a new tag means a new model"
    assert rig_model() is rig_model(), "... cached until the next one"
    assert tag in str(RigConfig.model_json_schema())


def test_a_build_that_fails_part_way_leaves_nothing_running_or_registered(fresh):
    from flyball.core.reading import Source

    name = fresh("half_built")
    document = {
        "links": {"p": {"tag": "sim_plant"}},
        "readers": [
            {
                "period_s": 0.01,
                "device": {
                    "tag": "sim_reader",
                    "name": name,
                    "link": "p",
                    "measurand": "t",
                    "unit": "°C",
                },
            }
        ],
        "actuators": [{"tag": "sim_actuator", "name": "h", "link": "p"}],
        "loops": [{"channel": f"{name}.t", "actuator": "h"}],
    }
    config = RigConfig.model_validate(document)
    Source.forget(name)
    document["loops"][0]["channel"] = f"{name}.nope"  # fails at the loop, after the reader is built
    broken = RigConfig.model_validate(document)
    with pytest.raises(NotFoundError):
        broken.build()
    rig = config.build(start=True)  # the retry: the source name is free, nothing was left polling
    try:
        assert rig.readers.run(name).running is True and list(rig.loops) == ["h"]
    finally:
        rig.readers.stop_all()
        Source.forget(name)
