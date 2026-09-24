"""The example rig loads through a board profile, and its sim overlay runs on the fakes."""

from pathlib import Path

import pytest
from flyball.foundation.device import Reading, Signal
from flyball.runtime.config import load_board, load_rig_config, resolve_documents
from flyball_sim import SteppedClock

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
BOARDS = Path(__file__).resolve().parents[1] / "src" / "flyball_linux" / "boards"
REAL = EXAMPLES / "greenhouse.yaml"
SIM = EXAMPLES / "sim.yaml"
SENSORS = EXAMPLES / "sensors"
SIM_CONFIGS = EXAMPLES / "sim-configs"


def test_the_real_file_resolves_its_pins_from_the_board():
    document, files = resolve_documents(REAL)
    assert files == [REAL, BOARDS / "rpi5.toml"]
    assert document["links"]["header"] == {"type": "gpio", "chip": "gpiochip4"}
    fan = document["devices"]["fan"]
    assert fan["link"] == "header" and fan["line"] == 18 and "pin" not in fan
    assert document["devices"]["heater"]["link"] == "pwm"
    config = load_rig_config(REAL)
    assert config.board == "rpi5" and len(config.links) == 5
    assert config.devices["heater"].driver_config == {
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
    assert all(tag.startswith("fake_") for tag in (link.type_name for link in sim.links.values()))
    assert sim.simulated


def test_the_sim_rig_builds_reads_and_regulates():
    rig = load_rig_config([REAL, SIM]).build(clock=SteppedClock(0), start=False)
    rig.devices["air"].sensor.sleep = False
    assert {p: str(s.access) for p, s in rig.devices["heater"].signals.items()} == {
        "drive": "rpw",
        "frequency_hz": "rp",
        "last.set_frequency": "rp",
        "last.off": "rp",
    }
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
    heater = rig.devices["heater"]
    assert heater.fraction(heater.written[heater.signals["drive"]].value) == pytest.approx(
        0.5, abs=0.05
    )
    fan = rig.devices["fan"]
    fan.on()
    assert fan.signals["on"].value == 1.0 and fan.link.levels[18] is True


def test_every_board_profile_validates():
    for path in BOARDS.glob("*.toml"):
        board = load_board(path)
        assert board.name, path
        for label, fields in board.pins.items():
            assert fields["link"] in board.links, f"{path}: pin {label} names an undeclared link"


@pytest.mark.parametrize(
    ("example", "adc", "curve", "unit"),
    [("turbidity", "turbidity_adc", "turbidity", "NTU"), ("ph_probe", "ph_adc", "ph", "pH")],
)
def test_an_analog_sensor_reads_its_channel_once_and_calibrates_it_with_a_curve(
    example, adc, curve, unit, tmp_path
):
    base = SENSORS / f"{example}.yaml"
    fake_bus = tmp_path / "bus.yaml"
    fake_bus.write_text("links:\n  i2c1: { type: fake_i2c }\n")
    real = load_rig_config([base, fake_bus]).build(clock=SteppedClock(0), start=False)
    assert list(real.devices[adc].signals) == ["raw_v"], "one conversion per poll, not two"
    rig = load_rig_config([base, SIM_CONFIGS / f"{example}.yaml"]).build(
        clock=SteppedClock(0), start=False
    )
    raw, value = rig.resolve(f"{adc}.raw_v"), rig.resolve(f"{curve}.value")
    assert isinstance(raw, Signal) and isinstance(value, Signal)
    assert not raw.spec.record and value.spec.record, "the store keeps the calibrated value"
    assert value.unit.symbol == unit
    volts = rig.read(raw, fresh=True).value
    engineering = rig.latest[value]
    assert engineering.usable, "the sim's baseline sits inside the curve's domain"
    assert engineering.value == pytest.approx(rig.devices[curve].curve(volts))
