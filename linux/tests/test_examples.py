"""The example rig files load through a board profile and run on the fakes."""

from pathlib import Path

import pytest
from flyball.core.reading import Source
from flyball.runtime.config import load_rig_config, resolve_document
from flyball.sim import SteppedClock

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
BOARDS = Path(__file__).resolve().parents[2] / "boards"


@pytest.fixture
def forget():
    """Example sources by name, forgotten after, so each test builds afresh."""
    yield
    for name in ("air", "soil"):
        Source.forget(name)


def test_the_real_file_resolves_its_pins_from_the_board(forget):
    document, board = resolve_document(EXAMPLES / "greenhouse.toml")
    assert board == BOARDS / "rpi5.toml"
    assert document["links"]["header"] == {"tag": "gpio", "chip": "gpiochip4"}
    fan = next(a for a in document["actuators"] if a["name"] == "fan")
    assert fan["link"] == "header" and fan["line"] == 18 and "pin" not in fan
    config = load_rig_config(EXAMPLES / "greenhouse.toml")
    assert config.board == "rpi5" and len(config.links) == 5


def test_the_sim_file_runs_end_to_end(forget):
    config = load_rig_config(EXAMPLES / "greenhouse.sim.toml")
    rig = config.build(clock=SteppedClock(0), start=False)
    for reader in rig.readers.by_name.values():
        rig.read(reader)
    air = rig.readers.by_name["air"]
    assert air.state.temperature == pytest.approx(21.5, abs=0.01)
    assert rig.readers.by_name["soil"].state.temperature == pytest.approx(21.875)
    (loop_name,) = rig.loops
    rig.loops[loop_name].regulate(25.0)
    assert rig.actuators["heater"].state.duty == pytest.approx(0.5, abs=0.05)
    assert rig.actuators["fan"].on().on is True


def test_every_board_profile_validates():
    from flyball.runtime.config import load_board

    for path in BOARDS.glob("*.toml"):
        board = load_board(path)
        assert board.name, path
        for label, fields in board.pins.items():
            assert fields["link"] in board.links, f"{path}: pin {label} names an undeclared link"
