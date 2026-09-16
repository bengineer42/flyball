"""The example rig loads through a board profile, and its sim overlay runs on the fakes."""

from pathlib import Path

import pytest
from flyball.core.signal import Reading, Signal
from flyball.runtime.config import load_board, load_rig_config, resolve_documents
from flyball.sim import SteppedClock

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
BOARDS = Path(__file__).resolve().parents[2] / "boards"
REAL = EXAMPLES / "greenhouse.yaml"
SIM = EXAMPLES / "sim.yaml"


def test_the_real_file_resolves_its_pins_from_the_board():
    document, files = resolve_documents(REAL)
    assert files == [REAL, BOARDS / "rpi5.toml"]
    assert document["links"]["header"] == {"tag": "gpio", "chip": "gpiochip4"}
    fan = document["devices"]["fan"]
    assert fan["link"] == "header" and fan["line"] == 18 and "pin" not in fan
    assert document["devices"]["heater"]["link"] == "pwm"
    config = load_rig_config(REAL)
    assert config.board == "rpi5" and len(config.links) == 5
    assert config.devices["heater"].config == {
        "link": "pwm",
        "channel": 0,
        "frequency_hz": 1000,
        "unit": "°C",
        "quantity": "temperature",
        "span": [10, 40],
    }


def test_the_overlay_keeps_every_name_and_address_on_fake_links():
    real = load_rig_config(REAL)
    sim = load_rig_config([REAL, SIM])
    assert sim.board == "sim" and sim.name == "greenhouse"
    assert set(sim.devices) == set(real.devices)
    assert {e.driver for e in sim.devices.values()} == {e.driver for e in real.devices.values()}
    assert all(tag.startswith("fake_") for tag in (link.config_tag for link in sim.links.values()))
    assert sim.simulated


def test_the_sim_rig_builds_reads_and_regulates():
    rig = load_rig_config([REAL, SIM]).build(clock=SteppedClock(0), start=False)
    rig.devices["air"].sensor.sleep = False
    assert {p: str(s.access) for p, s in rig.devices["heater"].signals.items()} == {"drive": "w"}
    temperature = rig.resolve("air.temperature")
    assert isinstance(temperature, Signal)
    reading = rig.read(temperature, fresh=True)
    assert isinstance(reading, Reading) and reading.value == pytest.approx(21.5, abs=0.01)
    soil = rig.resolve("soil.temperature")
    assert isinstance(soil, Signal)
    assert rig.read(soil, fresh=True).value == pytest.approx(21.875)
    controller = rig.controllers.resolve(None)
    assert controller.name == "heater.drive"
    controller.regulate(25.0)
    rig.on_samples(list(rig.devices["air"].read(1_000_000_000)))
    assert rig.devices["heater"].state.duty == pytest.approx(0.5, abs=0.05)
    fan = rig.devices["fan"]
    assert fan.on().level is True and fan.link.levels[18] is True


def test_every_board_profile_validates():
    for path in BOARDS.glob("*.toml"):
        board = load_board(path)
        assert board.name, path
        for label, fields in board.pins.items():
            assert fields["link"] in board.links, f"{path}: pin {label} names an undeclared link"
