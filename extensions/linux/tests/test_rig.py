"""Every driver read and committed through a rig built from a rig file, on the fakes."""

import pytest
from flyball.foundation.device import Reading, Signal
from flyball.foundation.errors import ConflictError
from flyball.runtime.config import RigConfig, rig_schema
from flyball_chips.sht4x import encode
from flyball_sim import SteppedClock

NS = 1_000_000_000
W1_TEXT = "5e 01 4b 46 7f ff 0c 10 4e : crc=4e YES\n5e 01 4b 46 7f ff 0c 10 4e t=21875"


def bench_document() -> dict:
    """One of every driver, flat and layered, on scripted fakes."""
    return {
        "name": "bench",
        "links": {
            "i2c1": {
                "type": "fake_i2c",
                "registers": {0x48: {0x00: [0x40, 0x00]}, 0x4A: {0x00: [0x0C, 0x80], 0x01: [0, 0]}},
                "replies": {
                    0x44: [list(encode(21.5, 55.0))],
                    0x45: [list(encode(22.0, 5.0))],
                    0x46: [list(encode(23.0, 95.0))],
                },
            },
            "spi0": {"type": "fake_spi", "replies": [[0, 0x03, 0xFF]]},
            "header": {"type": "fake_gpio", "levels": {17: True}},
            "pwm": {"type": "fake_pwm"},
            "w1": {"type": "fake_onewire", "texts": {"28-1": W1_TEXT}},
        },
        "devices": {
            "air": {"driver": "sht4x", "poll_s": 2, "link": "i2c1"},
            "hum": {
                "driver": "sht4x_set",
                "poll_s": 1,
                "config": {
                    "link": "i2c1",
                    "sensors": {"dry": {"address": 0x45}, "wet": {"address": 0x46}},
                },
                "signals": {
                    "wet": {"poll_s": 5},
                    "dry": {"signals": {"humidity": {"warning": [0, 10]}}},
                },
            },
            "adc": {
                "driver": "ads1115",
                "poll_s": 1,
                "link": "i2c1",
                "channels": {"pressure": {"channel": 1, "scale": 25.0, "unit": "kPa"}},
                "signals": {"pressure": {"range": [0, 100], "precision": 1}},
            },
            "pot": {
                "driver": "mcp3008",
                "poll_s": 1,
                "link": "spi0",
                "channels": {"level": {"channel": 0}},
            },
            "chip": {
                "driver": "i2c_table",
                "poll_s": 1,
                "link": "i2c1",
                "address": 0x4A,
                "registers": {
                    "temperature": {"address": 0, "signed": True, "scale": 0.0078125, "unit": "°C"},
                    "setpoint": {"address": 1, "scale": 0.5, "unit": "°C", "write": True},
                },
            },
            "door": {
                "driver": "gpio_line",
                "poll_s": 1,
                "link": "header",
                "line": 17,
                "direction": "input",
            },
            "fan": {"driver": "gpio_line", "link": "header", "line": 18},
            "heater": {
                "driver": "pwm_channel",
                "link": "pwm",
                "channel": 0,
                "unit": "°C",
                "quantity": "temperature",
                "span": [10, 40],
                "signals": {"drive": {"limits": [10, 34]}},
            },
            "soil": {"driver": "ds18b20", "poll_s": 5, "link": "w1", "device": "28-1"},
        },
        "controllers": {
            "heater.drive": {
                "measured": "air.temperature",
                "law": {"type": "PI", "kp": 1.0, "ki": 0.0},
                "default": True,
            }
        },
    }


@pytest.fixture
def rig():
    rig = RigConfig.model_validate(bench_document()).build(clock=SteppedClock(0), start=False)
    for name in ("air", "hum", "adc"):
        for sensor in getattr(rig.devices[name], "sensors", {"": rig.devices[name]}).values():
            sensor.sleep = False  # no conversion wait against a fake
    return rig


def _signal(rig, address: str) -> Signal:
    target = rig.resolve(address)
    assert isinstance(target, Signal)
    return target


def test_the_document_validates_against_every_registered_tag():
    config = RigConfig.model_validate(bench_document())
    assert set(config.devices) == {
        "air",
        "hum",
        "adc",
        "pot",
        "chip",
        "door",
        "fan",
        "heater",
        "soil",
    }
    assert config.devices["adc"].config["channels"] == {
        "pressure": {"channel": 1, "scale": 25.0, "unit": "kPa"}
    }, "flat driver settings land under config"


def test_the_tree_and_the_envelope_s_overrides(rig):
    assert {p: str(s.access) for p, s in rig.devices["chip"].signals.items()} == {
        "conditions": "rp",
        "temperature": "rp",
        "setpoint": "rpw",
    }
    assert _signal(rig, "hum.dry.humidity").spec.warning == (0.0, 10.0)
    assert _signal(rig, "hum.wet.humidity").poll_s == 5.0
    assert _signal(rig, "adc.pressure").spec.range == (0.0, 100.0)
    assert _signal(rig, "heater.drive").limits == (10.0, 34.0), "the file narrowed the span"
    assert _signal(rig, "heater.drive").spec.limits == (10.0, 40.0), "the driver's span stays"
    assert _signal(rig, "heater.drive").narrowed == (10.0, 34.0)


@pytest.mark.parametrize(
    ("address", "value"),
    [
        ("air.temperature", 21.5),
        ("air.humidity", 55.0),
        ("hum.dry.humidity", 5.0),
        ("hum.wet.temperature", 23.0),
        ("adc.pressure", 2.048 * 25.0),
        ("pot.level", 3.3),
        ("chip.temperature", 25.0),
        ("door.level", 1.0),
        ("soil.temperature", 21.875),
    ],
)
def test_every_publishing_signal_reads_fresh_through_the_rig(rig, address, value):
    reading = rig.read(_signal(rig, address), fresh=True)
    assert isinstance(reading, Reading)
    assert reading.value == pytest.approx(value, abs=0.01)
    assert rig.latest[_signal(rig, address)] is reading


def test_a_namespace_reads_as_one_sample(rig):
    dry = rig.resolve("hum.dry")
    sample = rig.read(dry, fresh=True)
    assert sample.node is dry
    assert sample.by_name() == {
        "humidity": pytest.approx(5.0, abs=0.01),
        "temperature": pytest.approx(22.0, abs=0.01),
    }


def test_a_demand_commits_through_the_fake_bus(rig):
    chip = rig.devices["chip"]
    states = rig.write(chip.root, {"setpoint": 25.3})
    setpoint = _signal(rig, "chip.setpoint")
    assert chip.link.written == [(0x4A, 0x01, [0x00, 0x33])], "(25.5 / 0.5) = 51 = 0x33"
    assert states[setpoint].value == pytest.approx(25.5), "what the chip holds, quantised"
    fan = rig.devices["fan"]
    assert rig.write(fan.root, {"on": 1})[_signal(rig, "fan.on")].value == 1.0
    assert fan.link.levels[18] is True
    states = rig.write(fan.root, {"on": 3})
    assert states[_signal(rig, "fan.on")].requested == 3.0, "clamped to limits [0, 1]"


def test_a_controller_drives_the_heater_and_a_manual_demand_is_refused(rig):
    heater = rig.devices["heater"]
    drive = _signal(rig, "heater.drive")
    controller = rig.controllers.resolve(None)
    assert controller.name == "heater.drive" and controller.output_unit == "°C"
    controller.regulate(25.0)
    rig.on_samples(list(rig.devices["air"].read(1 * NS)))
    assert heater.written[drive].controller == "heater.drive"
    assert heater.written[drive].value == pytest.approx(25.0 + (25.0 - 21.5), abs=0.01), (
        "kp 1: reference plus the error, in °C"
    )
    assert heater.fraction(heater.written[drive].value) == pytest.approx(
        (28.5 - 10) / 30, abs=0.001
    )
    with pytest.raises(ConflictError, match="driven by controller"):
        rig.write(heater.root, {"drive": 20.0})
    controller.regulate(60.0)
    rig.on_samples(list(rig.devices["air"].read(2 * NS)))
    assert heater.written[drive].value == 34.0 and heater.written[drive].at_limit == "high"


def test_the_entry_point_registers_every_tag():
    from flyball.model.catalog import Catalogs

    fresh = Catalogs()
    assert "linux" in fresh.discover()
    for tag in (
        "sht4x",
        "sht4x_set",
        "ads1115",
        "mcp3008",
        "i2c_table",
        "gpio_line",
        "pwm_channel",
        "ds18b20",
    ):
        assert fresh.devices[tag].type_name == tag
    for tag in ("i2c", "spi", "gpio", "pwm", "onewire"):
        assert fresh.links[tag].type_name == tag


def test_the_schema_describes_every_driver_flat_and_layered():
    by_driver = rig_schema()["properties"]["devices"]["additionalProperties"]
    tags = {
        shape["properties"]["driver"]["const"]
        for variant in by_driver["oneOf"]
        # The last variants are the layer forms -- an entry that only adds to a device a base
        # declared, and `null` to remove one -- neither of which names a driver.
        for shape in variant.get("oneOf", [])
    }
    assert {
        "sht4x",
        "sht4x_set",
        "ads1115",
        "mcp3008",
        "i2c_table",
        "gpio_line",
        "pwm_channel",
        "ds18b20",
    } <= tags
