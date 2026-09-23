"""Board profiles: found on the board path, merged under the file, pins resolved."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from flyball.foundation.config import Config
from flyball.foundation.device import (
    Access,
    Device,
    DriverConfig,
    Readable,
    Role,
    Sample,
    SignalSpec,
)
from flyball.foundation.errors import NotFoundError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Watt
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


class Relay(Device):
    """One W signal, on a bus at a unit id: what a board's pin resolves to."""

    TREE = (
        SignalSpec(
            name="power", quantity=Quantity("power", Watt), access=Access.RPW, role=Role.DEMAND
        ),
    )

    def __init__(self, name: str, unit_id: int, label: str | None = None) -> None:
        super().__init__(name, label)
        self.unit_id = unit_id


class RelayConfig(DriverConfig[Relay]):
    unit_id: int

    def build(self, name: str, label: str | None = None) -> Relay:
        return Relay(name, self.unit_id, label)


@pytest.fixture
def relay_tag(fresh, _catalog) -> str:
    tag = fresh("relay")

    class Tagged(RelayConfig, tag=tag):
        pass

    _catalog.register_device(Tagged)
    return tag


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


def test_apply_board_merges_links_and_resolves_pins_flat_or_layered():
    board = Board.model_validate(__import__("tomllib").loads(BOARD))
    document = {
        "links": {"bus": {"tag": "fake_registers", "registers": {"1": 5}}},
        "devices": {
            "flat": {"driver": "relay", "pin": "OUT1", "label": "Flat"},
            "own": {"driver": "relay", "pin": "OUT1", "unit_id": 2},
            "layered": {"driver": "relay", "config": {"pin": "OUT1"}},
            "beside": {"driver": "relay", "pin": "OUT1", "config": {"unit_id": 3}},
            "plain": {"driver": "relay", "unit_id": 4},
        },
    }
    out = apply_board(document, board)
    assert out["links"]["bus"] == {"tag": "fake_registers", "registers": {"1": 5}}, "file wins"
    assert out["links"]["plant"] == {"tag": "sim_plant"}
    assert out["devices"]["flat"] == {
        "driver": "relay",
        "label": "Flat",
        "link": "bus",
        "unit_id": 7,
    }
    assert out["devices"]["own"]["unit_id"] == 2, "an entry's own field wins over the pin's"
    assert out["devices"]["layered"] == {"driver": "relay", "config": {"link": "bus", "unit_id": 7}}
    assert out["devices"]["beside"] == {
        "driver": "relay",
        "config": {"link": "bus", "unit_id": 3},
    }, "a pin beside `config` resolves into it, so the entry stays layered"
    assert out["devices"]["plain"] == {"driver": "relay", "unit_id": 4}
    assert document["devices"]["flat"]["pin"] == "OUT1", "the input is untouched"
    with pytest.raises(NotFoundError, match="devices.x: pin 'NOPE' is not on this board"):
        apply_board({"devices": {"x": {"pin": "NOPE"}}}, board)


def test_a_rig_file_with_a_board_validates_builds_and_reports_it(tmp_path, monkeypatch, relay_tag):
    monkeypatch.setenv(BOARDS_ENV, str(tmp_path / "profiles"))
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "test.toml").write_text(BOARD)
    rig_file = tmp_path / "rig.toml"
    rig_file.write_text(f'board = "test"\n[devices.valve]\ndriver = "{relay_tag}"\npin = "OUT1"\n')
    document, board_path = resolve_document(rig_file)
    assert board_path == tmp_path / "profiles" / "test.toml"
    config = RigConfig.model_validate(document)
    assert config.board == "test" and set(config.links) == {"bus", "plant"}
    assert config.devices["valve"].config == {"link": "bus", "unit_id": 7}
    rig = config.build(start=False)
    valve = rig.devices["valve"]
    assert isinstance(valve, Relay) and valve.unit_id == 7


def test_roles_tell_drivers_from_links(_catalog):
    assert role_of(_catalog.devices["sim_daq"]) == "driver"
    assert role_of(_catalog.links["visa"]) == "link"
    assert _catalog.devices["sim_drive"] in registered("driver", _catalog)
    assert _catalog.links["sim_plant"] in registered("link", _catalog)
    assert not set(registered("driver", _catalog)) & set(registered("link", _catalog))


def test_a_link_registered_later_is_valid_in_a_file(fresh, _catalog):
    tag = fresh("late_bus")
    before = rig_model(_catalog)

    class LateBus(Config[object], tag=tag):
        """Registered after the module was imported."""

        baud: int = 9600

        def build(self) -> object:
            return object()

    _catalog.register_link(LateBus)
    config = RigConfig.model_validate({"links": {"b": {"tag": tag, "baud": 115200}}})
    assert isinstance(config, RigConfig) and isinstance(config.links["b"], LateBus)
    assert config.links["b"].baud == 115200
    assert rig_model(_catalog) is not before, "a new tag means a new model"
    assert rig_model(_catalog) is rig_model(_catalog), "... cached until the next one"
    assert tag in str(RigConfig.model_json_schema())


class Daq(Readable):
    TREE = (SignalSpec(name="t", quantity=Quantity("temperature", "°C"), access=Access.RP),)

    def read(self, time_ns: int, node=None) -> Iterator[Sample]:
        yield Sample(self.root, time_ns, {self.signals["t"]: 20.0})


class DaqConfig(DriverConfig[Daq]):
    def build(self, name: str, label: str | None = None) -> Daq:
        return Daq(name, label)


def test_a_build_that_fails_part_way_leaves_nothing_running_or_registered(fresh, _catalog):
    daq_tag, relay_tag = fresh("daq"), fresh("relay")

    class TaggedDaq(DaqConfig, tag=daq_tag):
        pass

    class TaggedRelay(RelayConfig, tag=relay_tag):
        pass

    _catalog.register_device(TaggedDaq)
    _catalog.register_device(TaggedRelay)
    name = fresh("probe")
    document = {
        "devices": {
            name: {"driver": daq_tag, "poll_s": 0.01},
            "h": {"driver": relay_tag, "unit_id": 1},
        },
        "controllers": {"h.power": {"signal": f"{name}.t"}},
    }
    config = RigConfig.model_validate(document)
    document["controllers"] = {"h.power": {"signal": f"{name}.nope"}}  # fails after the devices
    broken = RigConfig.model_validate(document)
    with pytest.raises(NotFoundError, match="nope"):
        broken.build()
    rig = config.build(start=True)  # the retry: nothing was left polling or claimed
    try:
        assert rig.polling.run(name).running is True and list(rig.controllers) == ["h.power"]
    finally:
        rig.close()
